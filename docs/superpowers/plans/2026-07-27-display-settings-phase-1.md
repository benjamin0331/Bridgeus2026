# 階段一：Supervisor 顯示設定層 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 Supervisor（研究者）能在設定頁開關議題（分角色）、調整每個議題的立場分流門檻、設定配對逾時分鐘數，且改門檻不會回頭改變既有對話的立場分類。

**Architecture:** 覆寫層（overlay）。`TOPIC_CONFIGS` / `SURVEY_CONFIGS` 仍是議題內容的真實來源，新增兩個 DB model 只存被改過的值；新增 `api/display_settings.py` 作為唯一讀取入口，把覆寫值 merge 到預設值上。門檻只有一個讀取點（`_get_survey_scoring_config`）且無快取，接上它即全面生效。

**Tech Stack:** Django 6 + DRF、pytest、React + Vite。

**依據 spec:** `docs/superpowers/specs/2026-07-27-supervisor-display-settings-and-mixed-entry-design.md`（§5 §6 §7.1 §7.2 §9 §10 §11 §12）

---

## 執行前必讀

- 後端指令一律在 `backend/` 目錄下用 `uv run` 執行。
- **測試必須序列執行**：後端測試共用同一個 Postgres test database，同時跑兩個測試程序會失敗。任何時候只跑一個 pytest 程序。
- 完整測試套件很慢（單一檔案約 1–5 分鐘，因為會 import torch / sentence-transformers）。每個 Task 只跑該 Task 的測試檔，最後再跑一次迴歸。
- 本階段**不碰**身份層的已知問題（見 `docs/superpowers/specs/2026-07-26-backend-identity-code-review.md`），唯一例外是 Task 5 必須修的 `JWTStatelessUserAuthentication`——那是本功能的阻擋問題。

---

## File Structure

| 檔案 | 責任 |
|---|---|
| `backend/api/models.py`（修改） | 新增 `PlatformDisplaySetting`、`TopicDisplayOverride` 兩個 model |
| `backend/api/migrations/0019_*.py`（新增） | 上述兩個 model 的 migration |
| `backend/api/display_settings.py`（新增） | **唯一**的顯示設定讀取入口：merge 覆寫值與預設值 |
| `backend/api/permissions.py`（修改） | 抽出 `user_is_researcher(user)` 供權限類別與讀取層共用 |
| `backend/api/views.py`（修改） | 接上門檻覆寫、議題清單過濾、顯示路徑 stance 修正、設定 API views |
| `backend/api/serializers.py`（修改） | 設定 API 的輸入驗證 |
| `backend/api/urls.py`（修改） | 三個設定端點路由 |
| `backend/api/admin.py`（修改） | 兩個新 model 註冊到 admin |
| `backend/api/tests_display_settings.py`（新增） | 讀取層 + 設定 API + 過濾 + stance 修正的測試 |
| `frontend/src/pages/SettingsPage.jsx`（修改） | 「顯示設定」面板 |
| `frontend/src/pages/SettingsPage.css`（修改） | 面板樣式 |

---

## Task 1: 新增兩個設定 model

**Files:**
- Modify: `backend/api/models.py`（檔尾追加）
- Create: `backend/api/migrations/0019_platformdisplaysetting_topicdisplayoverride.py`（由 makemigrations 產生）
- Test: `backend/api/tests_display_settings.py`

- [ ] **Step 1: 寫失敗的測試**

建立 `backend/api/tests_display_settings.py`：

```python
"""Supervisor 顯示設定（覆寫層）。

TOPIC_CONFIGS / SURVEY_CONFIGS 仍是議題內容的真實來源，這裡的 model 只存
被 Supervisor 改過的值；沒有對應列或欄位為 null＝沿用程式碼預設值。
見 docs/superpowers/specs/2026-07-27-supervisor-display-settings-and-mixed-entry-design.md
"""

from django.test import TestCase

from api.models import PlatformDisplaySetting, TopicDisplayOverride


class PlatformDisplaySettingModelTests(TestCase):
    def test_load_creates_singleton_with_defaults(self):
        setting = PlatformDisplaySetting.load()

        self.assertEqual(setting.pk, 1)
        self.assertEqual(
            setting.participant_entry_mode, PlatformDisplaySetting.EntryMode.MIXED
        )
        self.assertEqual(
            setting.researcher_entry_mode, PlatformDisplaySetting.EntryMode.SPLIT
        )
        self.assertEqual(setting.match_fallback_timeout_minutes, 5)

    def test_load_is_idempotent(self):
        first = PlatformDisplaySetting.load()
        first.match_fallback_timeout_minutes = 9
        first.save()

        second = PlatformDisplaySetting.load()

        self.assertEqual(second.pk, first.pk)
        self.assertEqual(second.match_fallback_timeout_minutes, 9)
        self.assertEqual(PlatformDisplaySetting.objects.count(), 1)

    def test_save_forces_single_row(self):
        PlatformDisplaySetting.load()
        extra = PlatformDisplaySetting(match_fallback_timeout_minutes=42)

        extra.save()

        self.assertEqual(PlatformDisplaySetting.objects.count(), 1)
        self.assertEqual(
            PlatformDisplaySetting.load().match_fallback_timeout_minutes, 42
        )


class TopicDisplayOverrideModelTests(TestCase):
    def test_defaults_are_visible_and_thresholds_null(self):
        override = TopicDisplayOverride.objects.create(topic_id=102)

        self.assertTrue(override.visible_to_participant)
        self.assertTrue(override.visible_to_researcher)
        self.assertIsNone(override.support_threshold)
        self.assertIsNone(override.oppose_threshold)

    def test_topic_id_is_unique(self):
        from django.db import IntegrityError

        TopicDisplayOverride.objects.create(topic_id=102)

        with self.assertRaises(IntegrityError):
            TopicDisplayOverride.objects.create(topic_id=102)
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd backend && uv run pytest api/tests_display_settings.py -v
```

Expected: FAIL — `ImportError: cannot import name 'PlatformDisplaySetting' from 'api.models'`

- [ ] **Step 3: 新增 model**

在 `backend/api/models.py` **檔尾**追加：

```python
class PlatformDisplaySetting(models.Model):
    """全站前端顯示設定。刻意只允許一列（pk=1），一律用 load() 取得。

    入口模式分角色：受試者預設走混合入口（後端依立場分流），研究者預設
    走分開入口（AI／配對兩張卡）方便測試。
    """

    class EntryMode(models.TextChoices):
        MIXED = "mixed", "混合入口"
        SPLIT = "split", "分開入口"

    participant_entry_mode = models.CharField(
        max_length=16,
        choices=EntryMode.choices,
        default=EntryMode.MIXED,
        help_text="一般使用者看到的入口形式",
    )
    researcher_entry_mode = models.CharField(
        max_length=16,
        choices=EntryMode.choices,
        default=EntryMode.SPLIT,
        help_text="研究者看到的入口形式",
    )
    match_fallback_timeout_minutes = models.PositiveIntegerField(
        default=5,
        help_text="配對等待超過這個分鐘數後，詢問使用者要不要改跟 AI 對話",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        # 單列表：任何 save 都寫在 pk=1，避免出現第二組互相打架的設定。
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "PlatformDisplaySetting":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return (
            f"participant={self.participant_entry_mode} "
            f"researcher={self.researcher_entry_mode}"
        )


class TopicDisplayOverride(models.Model):
    """單一議題的顯示覆寫。

    沒有對應列＝該議題全部沿用程式碼預設（雙角色可見、門檻用
    SURVEY_CONFIGS 的值）。門檻欄位為 null 代表「沒被改過」，不是 0。
    """

    topic_id = models.IntegerField(unique=True, db_index=True)
    visible_to_participant = models.BooleanField(default=True)
    visible_to_researcher = models.BooleanField(default=True)
    support_threshold = models.FloatField(
        null=True, blank=True, help_text="null＝沿用 SURVEY_CONFIGS 的預設門檻"
    )
    oppose_threshold = models.FloatField(
        null=True, blank=True, help_text="null＝沿用 SURVEY_CONFIGS 的預設門檻"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"topic={self.topic_id}"
```

- [ ] **Step 4: 產生 migration**

```bash
cd backend && uv run python manage.py makemigrations api
```

Expected: 產生 `api/migrations/0019_platformdisplaysetting_topicdisplayoverride.py`，內容含兩個 `CreateModel`。

- [ ] **Step 5: 執行測試確認通過**

```bash
cd backend && uv run pytest api/tests_display_settings.py -v
```

Expected: PASS（5 個測試）

- [ ] **Step 6: Commit**

```bash
git add backend/api/models.py backend/api/migrations/0019_platformdisplaysetting_topicdisplayoverride.py backend/api/tests_display_settings.py
git commit -m "feat(m2): add platform display setting and topic override models"
```

---

## Task 2: 抽出 `user_is_researcher` 共用函式

**Files:**
- Modify: `backend/api/permissions.py:17-28`
- Test: `backend/api/tests_display_settings.py`

理由：讀取層要判斷角色，但不能重複實作一份「怎樣算研究者」的邏輯。抽成函式後 `IsResearcher` 也改用它，只有一份定義。

- [ ] **Step 1: 寫失敗的測試**

在 `backend/api/tests_display_settings.py` 追加：

```python
class UserIsResearcherHelperTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group

        from api.permissions import RESEARCHER_GROUP_NAME

        User = get_user_model()
        self.group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
        self.researcher = User.objects.create_user(
            username="ds_researcher", password="pw-strong-12345"
        )
        self.researcher.groups.add(self.group)
        self.participant = User.objects.create_user(
            username="ds_participant", password="pw-strong-12345"
        )

    def test_group_member_is_researcher(self):
        from api.permissions import user_is_researcher

        self.assertTrue(user_is_researcher(self.researcher))

    def test_plain_user_is_not_researcher(self):
        from api.permissions import user_is_researcher

        self.assertFalse(user_is_researcher(self.participant))

    def test_none_is_not_researcher(self):
        from api.permissions import user_is_researcher

        self.assertFalse(user_is_researcher(None))

    def test_anonymous_user_is_not_researcher(self):
        from django.contrib.auth.models import AnonymousUser

        from api.permissions import user_is_researcher

        self.assertFalse(user_is_researcher(AnonymousUser()))
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd backend && uv run pytest api/tests_display_settings.py::UserIsResearcherHelperTests -v
```

Expected: FAIL — `ImportError: cannot import name 'user_is_researcher' from 'api.permissions'`

- [ ] **Step 3: 實作**

在 `backend/api/permissions.py` 的 `RESEARCHER_GROUP_NAME = "研究者"` 之後、`class IsResearcher` 之前插入：

```python
def user_is_researcher(user) -> bool:
    """這個使用者算不算研究者。

    權限類別與顯示設定讀取層共用同一份定義，避免兩邊對「誰是研究者」
    的判斷長歪。注意 simplejwt 的 TokenUser.groups 是 EmptyManager，
    用它呼叫本函式永遠得到 False——需要角色判斷的 view 必須用預設的
    JWTAuthentication 拿到真正的 User。
    """
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and user.groups.filter(name=RESEARCHER_GROUP_NAME).exists()
    )
```

並把 `IsResearcher.has_permission` 改為：

```python
    def has_permission(self, request, view):
        return user_is_researcher(request.user)
```

- [ ] **Step 4: 執行測試確認通過**

```bash
cd backend && uv run pytest api/tests_display_settings.py::UserIsResearcherHelperTests -v
```

Expected: PASS（4 個測試）

- [ ] **Step 5: 確認既有權限測試沒被弄壞**

```bash
cd backend && uv run pytest api/tests_account_management.py api/tests_viewpoint_review.py -v
```

Expected: PASS（全部）

- [ ] **Step 6: Commit**

```bash
git add backend/api/permissions.py backend/api/tests_display_settings.py
git commit -m "refactor(m1): extract user_is_researcher helper for reuse"
```

---

## Task 3: 讀取層 `api/display_settings.py`

**Files:**
- Create: `backend/api/display_settings.py`
- Test: `backend/api/tests_display_settings.py`

- [ ] **Step 1: 寫失敗的測試**

在 `backend/api/tests_display_settings.py` 追加：

```python
class StanceThresholdOverlayTests(TestCase):
    """議題 102 在 SURVEY_CONFIGS 的預設門檻是 support=4.5 / oppose=3.5。"""

    def test_defaults_when_no_override_row(self):
        from api.display_settings import get_stance_thresholds

        self.assertEqual(get_stance_thresholds(topic_id=102), (4.5, 3.5))

    def test_override_row_with_null_thresholds_keeps_defaults(self):
        from api.display_settings import get_stance_thresholds

        TopicDisplayOverride.objects.create(topic_id=102)

        self.assertEqual(get_stance_thresholds(topic_id=102), (4.5, 3.5))

    def test_full_override_wins(self):
        from api.display_settings import get_stance_thresholds

        TopicDisplayOverride.objects.create(
            topic_id=102, support_threshold=5.0, oppose_threshold=3.0
        )

        self.assertEqual(get_stance_thresholds(topic_id=102), (5.0, 3.0))

    def test_partial_override_keeps_other_side_default(self):
        from api.display_settings import get_stance_thresholds

        TopicDisplayOverride.objects.create(topic_id=102, support_threshold=5.5)

        self.assertEqual(get_stance_thresholds(topic_id=102), (5.5, 3.5))

    def test_default_thresholds_ignores_override(self):
        from api.display_settings import default_stance_thresholds

        TopicDisplayOverride.objects.create(
            topic_id=102, support_threshold=6.0, oppose_threshold=2.0
        )

        self.assertEqual(default_stance_thresholds(topic_id=102), (4.5, 3.5))


class TopicVisibilityTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group

        from api.permissions import RESEARCHER_GROUP_NAME

        User = get_user_model()
        group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
        self.researcher = User.objects.create_user(
            username="vis_researcher", password="pw-strong-12345"
        )
        self.researcher.groups.add(group)
        self.participant = User.objects.create_user(
            username="vis_participant", password="pw-strong-12345"
        )

    def test_all_topics_visible_without_overrides(self):
        from api.dialogue_topics import TOPIC_CONFIGS
        from api.display_settings import visible_topics

        for is_researcher in (True, False):
            ids = {topic["id"] for topic in visible_topics(is_researcher=is_researcher)}
            self.assertEqual(ids, set(TOPIC_CONFIGS))

    def test_hidden_from_participant_only(self):
        from api.display_settings import is_topic_visible, visible_topics

        TopicDisplayOverride.objects.create(
            topic_id=102, visible_to_participant=False, visible_to_researcher=True
        )

        participant_ids = {t["id"] for t in visible_topics(is_researcher=False)}
        researcher_ids = {t["id"] for t in visible_topics(is_researcher=True)}

        self.assertNotIn(102, participant_ids)
        self.assertIn(102, researcher_ids)
        self.assertFalse(is_topic_visible(topic_id=102, is_researcher=False))
        self.assertTrue(is_topic_visible(topic_id=102, is_researcher=True))

    def test_unknown_topic_is_never_visible(self):
        from api.display_settings import is_topic_visible

        self.assertFalse(is_topic_visible(topic_id=999, is_researcher=True))

    def test_visible_topics_preserves_display_order(self):
        from api.dialogue_topics import get_dialogue_topics
        from api.display_settings import visible_topics

        expected = [topic["id"] for topic in get_dialogue_topics()]
        actual = [topic["id"] for topic in visible_topics(is_researcher=True)]

        self.assertEqual(actual, expected)


class EntryModeAndTimeoutTests(TestCase):
    def test_entry_mode_defaults_per_role(self):
        from api.display_settings import get_entry_mode

        self.assertEqual(get_entry_mode(is_researcher=False), "mixed")
        self.assertEqual(get_entry_mode(is_researcher=True), "split")

    def test_entry_mode_follows_setting(self):
        from api.display_settings import get_entry_mode

        setting = PlatformDisplaySetting.load()
        setting.participant_entry_mode = PlatformDisplaySetting.EntryMode.SPLIT
        setting.save()

        self.assertEqual(get_entry_mode(is_researcher=False), "split")

    def test_fallback_timeout_seconds(self):
        from api.display_settings import get_match_fallback_timeout_seconds

        self.assertEqual(get_match_fallback_timeout_seconds(), 300)

        setting = PlatformDisplaySetting.load()
        setting.match_fallback_timeout_minutes = 2
        setting.save()

        self.assertEqual(get_match_fallback_timeout_seconds(), 120)
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd backend && uv run pytest api/tests_display_settings.py -k "Overlay or Visibility or EntryMode" -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'api.display_settings'`

- [ ] **Step 3: 實作讀取層**

建立 `backend/api/display_settings.py`：

```python
"""顯示設定的唯一讀取入口（覆寫層）。

TOPIC_CONFIGS / SURVEY_CONFIGS 是議題內容的真實來源；TopicDisplayOverride 與
PlatformDisplaySetting 只存 Supervisor 實際改過的值。所有「這個議題看不看得
到」「門檻是多少」「入口是混合還是分開」的判斷都要走這裡，不要直接讀
TOPIC_CONFIGS / SURVEY_CONFIGS——否則覆寫會有讀不到的死角。

見 docs/superpowers/specs/2026-07-27-supervisor-display-settings-and-mixed-entry-design.md §6
"""

from .dialogue_topics import TOPIC_CONFIGS, get_dialogue_survey, get_dialogue_topics
from .models import PlatformDisplaySetting, TopicDisplayOverride

# SURVEY_CONFIGS 沒有設 stance_rules 時的最終保底值，與 views._get_survey_scoring_config
# 原本寫死的 fallback 相同。
FALLBACK_SUPPORT_THRESHOLD = 4.5
FALLBACK_OPPOSE_THRESHOLD = 3.5


def default_stance_thresholds(*, topic_id: int) -> tuple[float, float]:
    """程式碼裡的預設門檻，不看任何覆寫。設定頁要顯示「預設是多少」時用。"""
    survey_config = get_dialogue_survey(topic_id) or {}
    stance_rules = survey_config.get("stance_rules", {})
    return (
        float(stance_rules.get("support_threshold", FALLBACK_SUPPORT_THRESHOLD)),
        float(stance_rules.get("oppose_threshold", FALLBACK_OPPOSE_THRESHOLD)),
    )


def get_stance_thresholds(*, topic_id: int) -> tuple[float, float]:
    """實際生效的門檻，回傳 (support, oppose)。"""
    support, oppose = default_stance_thresholds(topic_id=topic_id)

    override = TopicDisplayOverride.objects.filter(topic_id=topic_id).first()
    if override is not None:
        if override.support_threshold is not None:
            support = float(override.support_threshold)
        if override.oppose_threshold is not None:
            oppose = float(override.oppose_threshold)

    return support, oppose


def is_topic_visible(*, topic_id: int, is_researcher: bool) -> bool:
    if topic_id not in TOPIC_CONFIGS:
        return False

    override = TopicDisplayOverride.objects.filter(topic_id=topic_id).first()
    if override is None:
        return True

    return (
        override.visible_to_researcher if is_researcher
        else override.visible_to_participant
    )


def visible_topics(*, is_researcher: bool) -> list[dict]:
    """這個角色看得到的議題清單，維持 get_dialogue_topics() 的排序與欄位。"""
    overrides = {
        override.topic_id: override
        for override in TopicDisplayOverride.objects.all()
    }

    topics = []
    for topic in get_dialogue_topics():
        override = overrides.get(topic["id"])
        if override is not None:
            visible = (
                override.visible_to_researcher if is_researcher
                else override.visible_to_participant
            )
            if not visible:
                continue
        topics.append(topic)

    return topics


def get_entry_mode(*, is_researcher: bool) -> str:
    setting = PlatformDisplaySetting.load()
    return (
        setting.researcher_entry_mode if is_researcher
        else setting.participant_entry_mode
    )


def get_match_fallback_timeout_seconds() -> int:
    return PlatformDisplaySetting.load().match_fallback_timeout_minutes * 60
```

- [ ] **Step 4: 執行測試確認通過**

```bash
cd backend && uv run pytest api/tests_display_settings.py -v
```

Expected: PASS（全部，約 21 個測試）

- [ ] **Step 5: Commit**

```bash
git add backend/api/display_settings.py backend/api/tests_display_settings.py
git commit -m "feat(m2): add display settings overlay read layer"
```

---

## Task 4: 讓門檻覆寫實際生效

**Files:**
- Modify: `backend/api/views.py:291-323`（`_get_survey_scoring_config`）
- Test: `backend/api/tests_display_settings.py`

`_get_survey_scoring_config` 是全專案唯一讀 `support_threshold` / `oppose_threshold` 的地方，而且沒有快取。改這一處，8 個既有呼叫點（含 `_resolve_stance_category`、`_compute_user_stance_score`）自動吃到覆寫值。

- [ ] **Step 1: 寫失敗的測試**

在 `backend/api/tests_display_settings.py` 追加：

```python
class ThresholdOverrideAffectsStanceCategoryTests(TestCase):
    """覆寫門檻後，立場分類的分界點要跟著移動。

    預設 support=4.5 / oppose=3.5：4.6 是 support、3.4 是 oppose、4.0 是 neutral。
    覆寫成 support=5.5 / oppose=2.5 後：4.6 與 3.4 都應變成 neutral。
    """

    def test_default_boundaries(self):
        from api.views import _resolve_stance_category

        self.assertEqual(
            _resolve_stance_category(topic_id=102, user_stance_score=4.6), "support"
        )
        self.assertEqual(
            _resolve_stance_category(topic_id=102, user_stance_score=3.4), "oppose"
        )

    def test_override_moves_boundaries(self):
        from api.views import _resolve_stance_category

        TopicDisplayOverride.objects.create(
            topic_id=102, support_threshold=5.5, oppose_threshold=2.5
        )

        self.assertEqual(
            _resolve_stance_category(topic_id=102, user_stance_score=4.6), "neutral"
        )
        self.assertEqual(
            _resolve_stance_category(topic_id=102, user_stance_score=3.4), "neutral"
        )
        self.assertEqual(
            _resolve_stance_category(topic_id=102, user_stance_score=5.6), "support"
        )
        self.assertEqual(
            _resolve_stance_category(topic_id=102, user_stance_score=2.4), "oppose"
        )

    def test_override_takes_effect_without_restart(self):
        """_get_survey_scoring_config 不能加快取，否則改設定要重啟才生效。"""
        from api.views import _resolve_stance_category

        self.assertEqual(
            _resolve_stance_category(topic_id=102, user_stance_score=4.6), "support"
        )

        TopicDisplayOverride.objects.create(topic_id=102, support_threshold=5.5)

        self.assertEqual(
            _resolve_stance_category(topic_id=102, user_stance_score=4.6), "neutral"
        )
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd backend && uv run pytest api/tests_display_settings.py::ThresholdOverrideAffectsStanceCategoryTests -v
```

Expected: `test_default_boundaries` PASS，`test_override_moves_boundaries` 與 `test_override_takes_effect_without_restart` FAIL（覆寫沒被讀到，4.6 仍是 support）

- [ ] **Step 3: 接上讀取層**

在 `backend/api/views.py` 的 import 區（`from .dialogue_topics import (...)` 之後）加入：

```python
from .display_settings import get_stance_thresholds
```

把 `_get_survey_scoring_config`（`api/views.py:291`）改為：

```python
def _get_survey_scoring_config(topic_id: int) -> dict:
    survey_config = get_dialogue_survey(topic_id) or {}
    scale_config = survey_config.get("scale", {})
    stance_rules = survey_config.get("stance_rules", {})
    likert_questions = survey_config.get("questions", [])
    # 門檻走覆寫層：Supervisor 在設定頁調過的值優先於 SURVEY_CONFIGS。
    # 這裡刻意不加快取，否則改設定要重啟服務才生效。
    support_threshold, oppose_threshold = get_stance_thresholds(topic_id=topic_id)

    return {
        "scale_min": int(scale_config.get("min", 1)),
        "scale_max": int(scale_config.get("max", 7)),
        "reverse_question_ids": {
            str(question_id)
            for question_id in stance_rules.get("reverse_question_ids", [])
        },
        "support_threshold": support_threshold,
        "oppose_threshold": oppose_threshold,
        "neutral_score": float(
            (
                float(scale_config.get("min", 1))
                + float(scale_config.get("max", 7))
            )
            / 2
        ),
        "likert_question_ids": {
            str(question["id"]) for question in likert_questions
        },
        "open_question_mappings": [
            {
                "id": question["id"],
                "code": question["code"],
            }
            for question in survey_config.get("open_questions", [])
        ],
    }
```

- [ ] **Step 4: 執行測試確認通過**

```bash
cd backend && uv run pytest api/tests_display_settings.py::ThresholdOverrideAffectsStanceCategoryTests -v
```

Expected: PASS（3 個測試）

- [ ] **Step 5: 確認既有 stance 測試沒被弄壞**

```bash
cd backend && uv run pytest api/tests.py -k "stance or Stance" -v
```

Expected: PASS（含 `api/tests.py:401-413` 的四個邊界測試）

- [ ] **Step 6: Commit**

```bash
git add backend/api/views.py backend/api/tests_display_settings.py
git commit -m "feat(m2): make stance thresholds respect supervisor overrides"
```

---

## Task 5: 議題清單依角色過濾（含 auth class 修正）

**Files:**
- Modify: `backend/api/views.py:877-884`（`DialogueTopicListView`）
- Test: `backend/api/tests_display_settings.py`

`DialogueTopicListView` 目前用 `JWTStatelessUserAuthentication`，它回傳的 `TokenUser.groups` 是 `EmptyManager`——`user_is_researcher()` 對它**永遠回 False**，角色過濾會全錯（研究者被當成一般使用者）。必須改用預設的 `JWTAuthentication`（拿掉 `authentication_classes`，讓它吃 settings 的預設值）。

- [ ] **Step 1: 寫失敗的測試**

在 `backend/api/tests_display_settings.py` 追加：

```python
from rest_framework import status
from rest_framework.test import APITestCase


class DialogueTopicListVisibilityTests(APITestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group

        from api.permissions import RESEARCHER_GROUP_NAME

        User = get_user_model()
        group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
        self.researcher = User.objects.create_user(
            username="topics_researcher", password="pw-strong-12345"
        )
        self.researcher.groups.add(group)
        self.participant = User.objects.create_user(
            username="topics_participant", password="pw-strong-12345"
        )

    def _topic_ids(self, user):
        self.client.force_authenticate(user=user)
        response = self.client.get("/api/dialogue/topics/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return {row["id"] for row in response.data}

    def test_both_roles_see_all_topics_by_default(self):
        from api.dialogue_topics import TOPIC_CONFIGS

        self.assertEqual(self._topic_ids(self.participant), set(TOPIC_CONFIGS))
        self.assertEqual(self._topic_ids(self.researcher), set(TOPIC_CONFIGS))

    def test_topic_hidden_from_participant_only(self):
        TopicDisplayOverride.objects.create(
            topic_id=102, visible_to_participant=False, visible_to_researcher=True
        )

        self.assertNotIn(102, self._topic_ids(self.participant))
        self.assertIn(102, self._topic_ids(self.researcher))

    def test_topic_hidden_from_everyone(self):
        TopicDisplayOverride.objects.create(
            topic_id=102, visible_to_participant=False, visible_to_researcher=False
        )

        self.assertNotIn(102, self._topic_ids(self.participant))
        self.assertNotIn(102, self._topic_ids(self.researcher))
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd backend && uv run pytest api/tests_display_settings.py::DialogueTopicListVisibilityTests -v
```

Expected: `test_both_roles_see_all_topics_by_default` PASS，另外兩個 FAIL（隱藏的議題仍出現在清單裡）

- [ ] **Step 3: 實作**

把 `backend/api/views.py:877-884` 的 `DialogueTopicListView` 改為：

```python
class DialogueTopicListView(APIView):
    """議題清單，依請求者角色過濾。

    刻意不使用 JWTStatelessUserAuthentication：它回傳的 TokenUser.groups 是
    EmptyManager，user_is_researcher() 對它永遠是 False，研究者會被當成一般
    使用者而看不到只對研究者開放的議題。這裡需要真正的 User，所以吃 settings
    裡的預設 JWTAuthentication。
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        topics = visible_topics(is_researcher=user_is_researcher(request.user))
        serializer = DialogueTopicSerializer(topics, many=True)
        return Response(serializer.data)
```

並在 import 區補上（`from .display_settings import get_stance_thresholds` 那行改為）：

```python
from .display_settings import get_stance_thresholds, visible_topics
```

以及把 `from .permissions import IsGodotServiceToken, IsResearcher, RESEARCHER_GROUP_NAME` 改為：

```python
from .permissions import (
    IsGodotServiceToken,
    IsResearcher,
    RESEARCHER_GROUP_NAME,
    user_is_researcher,
)
```

- [ ] **Step 4: 執行測試確認通過**

```bash
cd backend && uv run pytest api/tests_display_settings.py::DialogueTopicListVisibilityTests -v
```

Expected: PASS（3 個測試）

- [ ] **Step 5: 確認既有議題清單測試沒被弄壞**

```bash
cd backend && uv run pytest api/tests.py -k "topic or Topic" -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/api/views.py backend/api/tests_display_settings.py
git commit -m "feat(m2): filter dialogue topic list by viewer role"
```

---

## Task 6: 顯示路徑改用已儲存的立場分類

**Files:**
- Modify: `backend/api/views.py:236-269`（`_dialogue_session_response_payload`）
- Modify: `backend/api/views.py:1064-1090`（兩個呼叫點補 `user_id`）
- Modify: `backend/api/views.py:1176`（`DialogueSessionReplyView` 的回應）
- Test: `backend/api/tests_display_settings.py`

問題：`_resolve_stance_category()` 在顯示路徑被拿 `stance_score` 即時重算。門檻一改，**舊對話畫面上的立場分類會跟著變**，違反「改門檻不影響既有資料」。改成優先讀已存的 `UserStanceProfile.stance_category`。

- [ ] **Step 1: 寫失敗的測試**

在 `backend/api/tests_display_settings.py` 追加：

```python
class StoredStanceCategoryTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        from api.models import UserStanceProfile

        User = get_user_model()
        self.user = User.objects.create_user(
            username="stance_owner", password="pw-strong-12345"
        )
        # 4.6 在預設門檻下是 support，且已經被存起來
        UserStanceProfile.objects.create(
            user=self.user,
            topic_id=102,
            stance_score="4.60",
            stance_category="support",
            survey_answers={},
            survey_open_answers={},
        )

    def test_prefers_stored_category_over_recompute(self):
        from api.views import _display_stance_category

        # 門檻改成 5.5：即時重算會變 neutral，但已存的是 support
        TopicDisplayOverride.objects.create(topic_id=102, support_threshold=5.5)

        self.assertEqual(
            _display_stance_category(
                user_id=self.user.id, topic_id=102, stance_score=4.6
            ),
            "support",
        )

    def test_falls_back_to_recompute_without_profile(self):
        from api.views import _display_stance_category

        self.assertEqual(
            _display_stance_category(user_id=self.user.id, topic_id=103, stance_score=4.6),
            "support",
        )

    def test_returns_none_for_unusable_score(self):
        from api.views import _display_stance_category

        self.assertIsNone(
            _display_stance_category(user_id=self.user.id, topic_id=103, stance_score=None)
        )
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd backend && uv run pytest api/tests_display_settings.py::StoredStanceCategoryTests -v
```

Expected: FAIL — `ImportError: cannot import name '_display_stance_category' from 'api.views'`

- [ ] **Step 3: 新增 helper**

在 `backend/api/views.py` 的 `_resolve_stance_category` 定義之後（約 `:381`）加入：

```python
def _display_stance_category(
    *, user_id: int | None, topic_id, stance_score
) -> str | None:
    """顯示用的立場分類：優先取已儲存的值，取不到才即時重算。

    門檻是 Supervisor 可調的。若顯示時一律用當下門檻重算，改一次門檻就會
    回頭改變所有舊對話畫面上的立場分類——那不是「調設定」，那是改寫既有
    實驗資料的呈現。已存的分類才是這場對話當初實際被分到的組別。

    註：階段二會在這裡優先讀 DialogueEntryAssignment.stance_category
    （那是分流當下的權威紀錄），UserStanceProfile 退為第二順位。
    """
    try:
        topic_id = int(topic_id)
    except (TypeError, ValueError):
        return None

    if user_id is not None:
        stored = (
            UserStanceProfile.objects.filter(user_id=user_id, topic_id=topic_id)
            .values_list("stance_category", flat=True)
            .first()
        )
        if stored:
            return stored

    try:
        return _resolve_stance_category(
            topic_id=topic_id, user_stance_score=float(stance_score)
        )
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 4: 執行測試確認通過**

```bash
cd backend && uv run pytest api/tests_display_settings.py::StoredStanceCategoryTests -v
```

Expected: PASS（3 個測試）

- [ ] **Step 5: 接到三個顯示路徑**

把 `_dialogue_session_response_payload`（`api/views.py:236`）的簽章與 stance 區塊改為：

```python
def _dialogue_session_response_payload(
    *,
    session_record: dict,
    restored_from: str,
    user_id: int | None = None,
) -> dict:
    session_state = session_record.get("session") or {}
    history = session_state.get("history") or []
    stance_drift = session_state.get("stance_drift")
    stance_score = session_state.get("user_stance_score")
    stance_category = _display_stance_category(
        user_id=user_id,
        topic_id=session_record.get("topic_id"),
        stance_score=stance_score,
    )
```

（其餘 `return {...}` 內容不變。）

在 `DialogueSessionLatestView`（`api/views.py:1064`）與 `DialogueSessionDetailView`（`api/views.py:1085`）兩個呼叫點補上 `user_id`：

```python
            _dialogue_session_response_payload(
                session_record=session_record,
                restored_from=restored_from,
                user_id=request.user.id,
            )
```

把 `DialogueSessionReplyView` 回應中的 stance 欄位（`api/views.py:1176`）改為：

```python
                "stance_category": _display_stance_category(
                    user_id=request.user.id,
                    topic_id=session_record.get("topic_id"),
                    stance_score=session.user_stance_score,
                ),
```

> `DialogueSessionCreateView`（`api/views.py:1014`）刻意**不改**：那是剛建立的 session，`UserStanceProfile` 正好在同一個請求裡寫入，即時計算與已存值必然一致，且此時的重算就是權威來源。

- [ ] **Step 6: 寫端對端測試確認舊對話顯示不變**

在 `backend/api/tests_display_settings.py` 追加：

```python
class ThresholdChangeDoesNotRewriteHistoryTests(TestCase):
    def test_existing_profile_category_survives_threshold_change(self):
        from django.contrib.auth import get_user_model

        from api.models import UserStanceProfile

        User = get_user_model()
        user = User.objects.create_user(
            username="history_owner", password="pw-strong-12345"
        )
        UserStanceProfile.objects.create(
            user=user,
            topic_id=102,
            stance_score="4.60",
            stance_category="support",
            survey_answers={},
            survey_open_answers={},
        )

        TopicDisplayOverride.objects.create(
            topic_id=102, support_threshold=5.5, oppose_threshold=2.5
        )

        profile = UserStanceProfile.objects.get(user=user, topic_id=102)
        self.assertEqual(profile.stance_category, "support")

        from api.views import _dialogue_session_response_payload

        payload = _dialogue_session_response_payload(
            session_record={
                "session_id": "abc123",
                "topic_id": 102,
                "session": {"user_stance_score": 4.6, "history": []},
            },
            restored_from="cache",
            user_id=user.id,
        )
        self.assertEqual(payload["stance_category"], "support")
```

- [ ] **Step 7: 執行測試確認通過**

```bash
cd backend && uv run pytest api/tests_display_settings.py -v
```

Expected: PASS（全部）

- [ ] **Step 8: 確認既有 session 測試沒被弄壞**

```bash
cd backend && uv run pytest api/tests.py api/tests_session_cleanup.py -v
```

Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add backend/api/views.py backend/api/tests_display_settings.py
git commit -m "fix(m2): use stored stance category in display paths

門檻改成可由 Supervisor 調整後，顯示時即時重算會讓改一次門檻就回頭
改變所有舊對話的立場分類。改為優先讀已儲存的分類。"
```

---

## Task 7: 設定 API

**Files:**
- Modify: `backend/api/serializers.py`（檔尾追加）
- Modify: `backend/api/views.py`（`AccountPasswordResetView` 之後追加）
- Modify: `backend/api/urls.py:41` 之後
- Test: `backend/api/tests_display_settings.py`

- [ ] **Step 1: 寫失敗的測試**

在 `backend/api/tests_display_settings.py` 追加：

```python
class DisplaySettingsApiTests(APITestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group

        from api.permissions import RESEARCHER_GROUP_NAME

        User = get_user_model()
        group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
        self.researcher = User.objects.create_user(
            username="cfg_researcher", password="pw-strong-12345"
        )
        self.researcher.groups.add(group)
        self.participant = User.objects.create_user(
            username="cfg_participant", password="pw-strong-12345"
        )

    def test_participant_cannot_read_settings(self):
        self.client.force_authenticate(user=self.participant)
        response = self.client.get("/api/settings/display/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_participant_cannot_patch_settings(self):
        self.client.force_authenticate(user=self.participant)
        response = self.client.patch(
            "/api/settings/display/", {"match_fallback_timeout_minutes": 9}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_participant_cannot_patch_topic(self):
        self.client.force_authenticate(user=self.participant)
        response = self.client.patch(
            "/api/settings/display/topics/102/", {"visible_to_participant": False}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_researcher_reads_defaults(self):
        self.client.force_authenticate(user=self.researcher)
        response = self.client.get("/api/settings/display/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["platform"]["participant_entry_mode"], "mixed")
        self.assertEqual(response.data["platform"]["researcher_entry_mode"], "split")
        self.assertEqual(
            response.data["platform"]["match_fallback_timeout_minutes"], 5
        )

        row = next(r for r in response.data["topics"] if r["topic_id"] == 102)
        self.assertTrue(row["visible_to_participant"])
        self.assertTrue(row["visible_to_researcher"])
        self.assertEqual(row["support_threshold"], 4.5)
        self.assertEqual(row["default_support_threshold"], 4.5)
        self.assertFalse(row["is_threshold_overridden"])

    def test_researcher_updates_platform_settings(self):
        self.client.force_authenticate(user=self.researcher)
        response = self.client.patch(
            "/api/settings/display/",
            {"participant_entry_mode": "split", "match_fallback_timeout_minutes": 12},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        setting = PlatformDisplaySetting.load()
        self.assertEqual(setting.participant_entry_mode, "split")
        self.assertEqual(setting.match_fallback_timeout_minutes, 12)
        self.assertEqual(setting.updated_by_id, self.researcher.id)

    def test_timeout_out_of_range_is_rejected(self):
        self.client.force_authenticate(user=self.researcher)

        for bad in (0, 121):
            response = self.client.patch(
                "/api/settings/display/", {"match_fallback_timeout_minutes": bad}
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.assertEqual(PlatformDisplaySetting.load().match_fallback_timeout_minutes, 5)

    def test_researcher_toggles_topic_visibility(self):
        self.client.force_authenticate(user=self.researcher)
        response = self.client.patch(
            "/api/settings/display/topics/102/", {"visible_to_participant": False}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["visible_to_participant"])
        self.assertTrue(response.data["visible_to_researcher"])
        override = TopicDisplayOverride.objects.get(topic_id=102)
        self.assertFalse(override.visible_to_participant)
        self.assertEqual(override.updated_by_id, self.researcher.id)

    def test_researcher_overrides_thresholds_and_gets_warning(self):
        self.client.force_authenticate(user=self.researcher)
        response = self.client.patch(
            "/api/settings/display/topics/102/",
            {"support_threshold": 5.5, "oppose_threshold": 2.5},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["support_threshold"], 5.5)
        self.assertTrue(response.data["is_threshold_overridden"])
        self.assertIn("既有資料不會重算", response.data["warning"])

    def test_oppose_must_be_below_support(self):
        self.client.force_authenticate(user=self.researcher)
        response = self.client.patch(
            "/api/settings/display/topics/102/",
            {"support_threshold": 3.0, "oppose_threshold": 4.0},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(TopicDisplayOverride.objects.filter(topic_id=102).exists())

    def test_partial_override_validated_against_current_effective_value(self):
        """只送 support=3.0，但目前 oppose 是 3.5——合起來不合法，要擋。"""
        self.client.force_authenticate(user=self.researcher)
        response = self.client.patch(
            "/api/settings/display/topics/102/", {"support_threshold": 3.0}
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_threshold_outside_scale_is_rejected(self):
        self.client.force_authenticate(user=self.researcher)
        response = self.client.patch(
            "/api/settings/display/topics/102/", {"support_threshold": 9.0}
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reset_threshold_to_default_with_null(self):
        self.client.force_authenticate(user=self.researcher)
        self.client.patch(
            "/api/settings/display/topics/102/",
            {"support_threshold": 5.5, "oppose_threshold": 2.5},
        )

        response = self.client.patch(
            "/api/settings/display/topics/102/",
            {"support_threshold": None, "oppose_threshold": None},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["support_threshold"], 4.5)
        self.assertFalse(response.data["is_threshold_overridden"])

    def test_unknown_topic_returns_404(self):
        self.client.force_authenticate(user=self.researcher)
        response = self.client.patch(
            "/api/settings/display/topics/999/", {"visible_to_participant": False}
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd backend && uv run pytest api/tests_display_settings.py::DisplaySettingsApiTests -v
```

Expected: FAIL — 全部 404（路由不存在）

- [ ] **Step 3: 新增 serializers**

在 `backend/api/serializers.py` **檔尾**追加（並在檔案頂端的 `from .models import (...)` 補上 `PlatformDisplaySetting`）：

```python
class PlatformDisplaySettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlatformDisplaySetting
        fields = [
            "participant_entry_mode",
            "researcher_entry_mode",
            "match_fallback_timeout_minutes",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]

    def validate_match_fallback_timeout_minutes(self, value):
        if not 1 <= value <= 120:
            raise serializers.ValidationError("等待時間需介於 1 到 120 分鐘。")
        return value


class TopicDisplayOverrideSerializer(serializers.Serializer):
    """單一議題的顯示覆寫。四個欄位都選填；門檻傳 null＝還原成程式碼預設值。

    門檻驗證必須看「套用後的實際結果」而不是只看這次送來的欄位：只送
    support=3.0 但目前 oppose 是 3.5 的話，合起來是不合法的，得擋下來。
    """

    visible_to_participant = serializers.BooleanField(required=False)
    visible_to_researcher = serializers.BooleanField(required=False)
    support_threshold = serializers.FloatField(required=False, allow_null=True)
    oppose_threshold = serializers.FloatField(required=False, allow_null=True)

    def validate(self, attrs):
        from .display_settings import default_stance_thresholds, get_stance_thresholds

        topic_id = self.context["topic_id"]
        survey_config = get_dialogue_survey(topic_id) or {}
        scale_config = survey_config.get("scale", {})
        scale_min = float(scale_config.get("min", 1))
        scale_max = float(scale_config.get("max", 7))

        current_support, current_oppose = get_stance_thresholds(topic_id=topic_id)
        default_support, default_oppose = default_stance_thresholds(topic_id=topic_id)

        def resolve(field, current, default):
            if field not in attrs:
                return current
            value = attrs[field]
            return default if value is None else float(value)

        support = resolve("support_threshold", current_support, default_support)
        oppose = resolve("oppose_threshold", current_oppose, default_oppose)

        for label, value in (("支持門檻", support), ("反對門檻", oppose)):
            if not scale_min <= value <= scale_max:
                raise serializers.ValidationError(
                    f"{label}需介於 {scale_min} 到 {scale_max} 之間。"
                )

        if oppose >= support:
            raise serializers.ValidationError("反對門檻必須小於支持門檻。")

        return attrs
```

- [ ] **Step 4: 新增 views**

在 `backend/api/views.py` 的 `AccountPasswordResetView` 之後追加：

```python
def _topic_display_row(topic_id: int) -> dict:
    """設定頁用的單一議題狀態：目前生效值 + 程式碼預設值 + 是否被覆寫。"""
    override = TopicDisplayOverride.objects.filter(topic_id=topic_id).first()
    support, oppose = get_stance_thresholds(topic_id=topic_id)
    default_support, default_oppose = default_stance_thresholds(topic_id=topic_id)

    return {
        "topic_id": topic_id,
        "title": TOPIC_CONFIGS.get(topic_id, {}).get("title", ""),
        "visible_to_participant": (
            override.visible_to_participant if override else True
        ),
        "visible_to_researcher": (
            override.visible_to_researcher if override else True
        ),
        "support_threshold": support,
        "oppose_threshold": oppose,
        "default_support_threshold": default_support,
        "default_oppose_threshold": default_oppose,
        "is_threshold_overridden": bool(
            override
            and (
                override.support_threshold is not None
                or override.oppose_threshold is not None
            )
        ),
    }


class DisplaySettingsView(APIView):
    """研究者專用：全站顯示設定 + 每個議題的目前狀態。"""

    permission_classes = [IsResearcher]

    def get(self, request):
        return Response(
            {
                "platform": PlatformDisplaySettingSerializer(
                    PlatformDisplaySetting.load()
                ).data,
                "topics": [
                    _topic_display_row(topic_id)
                    for topic_id in sorted(
                        TOPIC_CONFIGS,
                        key=lambda tid: TOPIC_CONFIGS[tid].get("display_order", tid),
                    )
                ],
            }
        )

    def patch(self, request):
        setting = PlatformDisplaySetting.load()
        serializer = PlatformDisplaySettingSerializer(
            setting, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        return Response(PlatformDisplaySettingSerializer(setting).data)


class DisplaySettingsTopicView(APIView):
    """研究者專用：單一議題的可見性與門檻覆寫。"""

    permission_classes = [IsResearcher]

    THRESHOLD_WARNING = "門檻變更只影響之後填寫的問卷，既有資料不會重算。"

    def patch(self, request, topic_id: int):
        if topic_id not in TOPIC_CONFIGS:
            return Response(
                {"detail": "找不到這個議題。"}, status=status.HTTP_404_NOT_FOUND
            )

        serializer = TopicDisplayOverrideSerializer(
            data=request.data, context={"topic_id": topic_id}
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        override, _ = TopicDisplayOverride.objects.get_or_create(topic_id=topic_id)
        for field in (
            "visible_to_participant",
            "visible_to_researcher",
            "support_threshold",
            "oppose_threshold",
        ):
            if field in data:
                setattr(override, field, data[field])
        override.updated_by = request.user
        override.save()

        payload = _topic_display_row(topic_id)
        if "support_threshold" in data or "oppose_threshold" in data:
            payload["warning"] = self.THRESHOLD_WARNING
        return Response(payload)
```

在 `backend/api/views.py` 的 import 區補上對應項目：

```python
from .display_settings import (
    default_stance_thresholds,
    get_stance_thresholds,
    visible_topics,
)
```

`from .models import (...)` 補上 `PlatformDisplaySetting,` 與 `TopicDisplayOverride,`。

`from .serializers import (...)` 補上 `PlatformDisplaySettingSerializer,` 與 `TopicDisplayOverrideSerializer,`。

- [ ] **Step 5: 新增路由**

在 `backend/api/urls.py` 的 `path('accounts/<int:pk>/reset-password/', ...)` 之後插入：

```python
    path('settings/display/', views.DisplaySettingsView.as_view()),
    path(
        'settings/display/topics/<int:topic_id>/',
        views.DisplaySettingsTopicView.as_view(),
    ),
```

- [ ] **Step 6: 執行測試確認通過**

```bash
cd backend && uv run pytest api/tests_display_settings.py::DisplaySettingsApiTests -v
```

Expected: PASS（13 個測試）

- [ ] **Step 7: 註冊到 admin**

在 `backend/api/admin.py` 的 import 區補上 `PlatformDisplaySetting,` 與 `TopicDisplayOverride,`，並在檔尾追加：

```python
@admin.register(PlatformDisplaySetting)
class PlatformDisplaySettingAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "participant_entry_mode",
        "researcher_entry_mode",
        "match_fallback_timeout_minutes",
        "updated_by",
        "updated_at",
    )


@admin.register(TopicDisplayOverride)
class TopicDisplayOverrideAdmin(admin.ModelAdmin):
    list_display = (
        "topic_id",
        "visible_to_participant",
        "visible_to_researcher",
        "support_threshold",
        "oppose_threshold",
        "updated_by",
        "updated_at",
    )
```

- [ ] **Step 8: 執行全套後端測試**

```bash
cd backend && uv run pytest api/ -v
```

Expected: PASS（全部；此步驟較慢，約 5–10 分鐘）

- [ ] **Step 9: Commit**

```bash
git add backend/api/serializers.py backend/api/views.py backend/api/urls.py backend/api/admin.py backend/api/tests_display_settings.py
git commit -m "feat(m2): add supervisor display settings API"
```

---

## Task 8: 前端「顯示設定」面板

**Files:**
- Modify: `frontend/src/pages/SettingsPage.jsx`
- Modify: `frontend/src/pages/SettingsPage.css`

前端無自動化測試，以手動驗收為準。面板放在既有帳號管理面板**之上**（顯示設定是比較常動的東西）。

- [ ] **Step 1: 加入資料載入與狀態**

在 `SettingsPage.jsx` 的 `function SettingsPage({ user })` 內、既有帳號 state 之後加入：

```jsx
  // 顯示設定
  const [displaySettings, setDisplaySettings] = useState(null);
  const [displayError, setDisplayError] = useState('');
  const [displayNotice, setDisplayNotice] = useState('');
  const [displayReloadKey, setDisplayReloadKey] = useState(0);

  const refreshDisplaySettings = () => setDisplayReloadKey((key) => key + 1);

  useEffect(() => {
    if (!isResearcher) return undefined;

    let cancelled = false;
    const loadDisplaySettings = async () => {
      setDisplayError('');
      try {
        const response = await api.get('/api/settings/display/');
        if (cancelled) return;
        setDisplaySettings(response.data);
      } catch (requestError) {
        if (cancelled) return;
        setDisplaySettings(null);
        setDisplayError(
          extractError(requestError, '目前無法讀取顯示設定。')
        );
      }
    };

    void loadDisplaySettings();

    return () => {
      cancelled = true;
    };
  }, [isResearcher, displayReloadKey]);
```

> `extractError` 已定義在元件內（`SettingsPage.jsx:67`），但宣告在這個 effect 之後。把 `extractError` 的定義**上移**到這個 effect 之前，避免 TDZ 問題。

- [ ] **Step 2: 加入更新處理函式**

在 `handleResetPassword` 之後加入：

```jsx
  const patchPlatformSetting = async (payload) => {
    setDisplayError('');
    setDisplayNotice('');
    try {
      await api.patch('/api/settings/display/', payload);
      refreshDisplaySettings();
    } catch (requestError) {
      setDisplayError(extractError(requestError, '更新顯示設定失敗。'));
    }
  };

  const patchTopicSetting = async (topicId, payload) => {
    setDisplayError('');
    setDisplayNotice('');
    try {
      const response = await api.patch(
        `/api/settings/display/topics/${topicId}/`,
        payload,
      );
      if (response.data?.warning) setDisplayNotice(response.data.warning);
      refreshDisplaySettings();
    } catch (requestError) {
      setDisplayError(extractError(requestError, '更新議題設定失敗。'));
    }
  };

  const handleThresholdBlur = (topic, field, rawValue) => {
    const trimmed = String(rawValue).trim();
    // 空字串＝還原成程式碼預設值
    if (trimmed === '') {
      void patchTopicSetting(topic.topic_id, { [field]: null });
      return;
    }
    const parsed = Number(trimmed);
    if (Number.isNaN(parsed)) {
      setDisplayError('門檻需為數字。');
      return;
    }
    if (parsed === topic[field]) return;
    void patchTopicSetting(topic.topic_id, { [field]: parsed });
  };
```

- [ ] **Step 3: 加入面板 JSX**

在 `return (` 的 `<div className="settings-page">` 內、`<form className="settings-create-form">` **之前**插入：

```jsx
      {displaySettings && (
        <section className="settings-display-panel">
          <h2>顯示設定</h2>

          {displayError && <div className="settings-action-error">{displayError}</div>}
          {displayNotice && <div className="settings-action-notice">{displayNotice}</div>}

          <div className="settings-display-row">
            <label>
              一般使用者入口
              <select
                value={displaySettings.platform.participant_entry_mode}
                onChange={(e) =>
                  patchPlatformSetting({ participant_entry_mode: e.target.value })
                }
              >
                <option value="mixed">混合入口（依立場自動分流）</option>
                <option value="split">分開入口（AI／配對各一）</option>
              </select>
            </label>

            <label>
              研究者入口
              <select
                value={displaySettings.platform.researcher_entry_mode}
                onChange={(e) =>
                  patchPlatformSetting({ researcher_entry_mode: e.target.value })
                }
              >
                <option value="mixed">混合入口（依立場自動分流）</option>
                <option value="split">分開入口（AI／配對各一）</option>
              </select>
            </label>

            <label>
              配對等待逾時（分鐘）
              <input
                type="number"
                min="1"
                max="120"
                defaultValue={displaySettings.platform.match_fallback_timeout_minutes}
                key={displaySettings.platform.match_fallback_timeout_minutes}
                onBlur={(e) => {
                  const parsed = Number(e.target.value);
                  if (
                    Number.isNaN(parsed) ||
                    parsed === displaySettings.platform.match_fallback_timeout_minutes
                  ) return;
                  void patchPlatformSetting({
                    match_fallback_timeout_minutes: parsed,
                  });
                }}
              />
            </label>
          </div>

          <table className="settings-table">
            <thead>
              <tr>
                <th>議題</th>
                <th>一般使用者可見</th>
                <th>研究者可見</th>
                <th>支持門檻</th>
                <th>反對門檻</th>
              </tr>
            </thead>
            <tbody>
              {displaySettings.topics.map((topic) => (
                <tr key={topic.topic_id}>
                  <td>{topic.title}</td>
                  <td>
                    <input
                      type="checkbox"
                      checked={topic.visible_to_participant}
                      onChange={(e) =>
                        patchTopicSetting(topic.topic_id, {
                          visible_to_participant: e.target.checked,
                        })
                      }
                    />
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      checked={topic.visible_to_researcher}
                      onChange={(e) =>
                        patchTopicSetting(topic.topic_id, {
                          visible_to_researcher: e.target.checked,
                        })
                      }
                    />
                  </td>
                  <td>
                    <input
                      type="number"
                      step="0.1"
                      key={`sup-${topic.topic_id}-${topic.support_threshold}`}
                      defaultValue={topic.support_threshold}
                      onBlur={(e) =>
                        handleThresholdBlur(topic, 'support_threshold', e.target.value)
                      }
                    />
                    <span className="settings-hint">
                      預設 {topic.default_support_threshold}
                    </span>
                  </td>
                  <td>
                    <input
                      type="number"
                      step="0.1"
                      key={`opp-${topic.topic_id}-${topic.oppose_threshold}`}
                      defaultValue={topic.oppose_threshold}
                      onBlur={(e) =>
                        handleThresholdBlur(topic, 'oppose_threshold', e.target.value)
                      }
                    />
                    <span className="settings-hint">
                      預設 {topic.default_oppose_threshold}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="settings-hint">
            門檻欄位清空後離開輸入框，即還原成程式碼預設值。變更門檻只影響之後填寫的問卷。
          </p>
        </section>
      )}
```

同時把標題區的 `<h1>帳號管理</h1>` 改為 `<h1>研究者設定</h1>`，說明文字改為
`<p>調整前端顯示與議題開關，或管理帳號。</p>`，並在帳號表格前補一個 `<h2>帳號管理</h2>`。

- [ ] **Step 4: 加入樣式**

在 `frontend/src/pages/SettingsPage.css` 檔尾追加：

```css
.settings-display-panel {
  margin-bottom: 32px;
  padding: 20px;
  border: 1px solid #e2e2e8;
  border-radius: 12px;
  background: #fff;
}

.settings-display-panel h2 {
  margin: 0 0 16px;
  font-size: 18px;
}

.settings-display-row {
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
  margin-bottom: 20px;
}

.settings-display-row label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 14px;
  color: #555;
}

.settings-display-row select,
.settings-display-row input {
  padding: 6px 10px;
  border: 1px solid #ccc;
  border-radius: 6px;
  font-size: 14px;
}

.settings-display-panel input[type='number'] {
  width: 72px;
  padding: 4px 8px;
  border: 1px solid #ccc;
  border-radius: 6px;
}

.settings-hint {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  color: #999;
}

.settings-action-notice {
  margin-bottom: 12px;
  padding: 10px 14px;
  border-radius: 8px;
  background: #fff7e6;
  color: #8a6d3b;
  font-size: 14px;
}
```

- [ ] **Step 5: 手動驗收**

啟動前後端：

```bash
cd backend && uv run uvicorn BridgeUs_Django.asgi:application --host 0.0.0.0 --port 8000 --reload
```

```bash
cd frontend && npm run dev
```

以研究者帳號登入 → 齒輪 → `/settings`，確認：

1. 「顯示設定」面板出現在帳號管理之上，三個下拉／輸入框顯示預設值（混合／分開／5）。
2. 取消勾選某議題的「一般使用者可見」→ 用一般使用者帳號登入，首頁該議題消失；研究者帳號仍看得到。
3. 把議題 102 的支持門檻改成 5.5 → 出現黃色提示「門檻變更只影響之後填寫的問卷，既有資料不會重算。」
4. 把支持門檻改成 3.0（低於反對門檻 3.5）→ 出現錯誤訊息，值不被套用。
5. 清空支持門檻欄位後離開輸入框 → 回到 4.5，且「預設 4.5」提示仍在。
6. 以一般使用者帳號打開 `/settings` → 只看到「尚無設定項」。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/SettingsPage.jsx frontend/src/pages/SettingsPage.css
git commit -m "feat(frontend): add supervisor display settings panel"
```

---

## 階段一驗收

- [ ] **執行完整後端測試**

```bash
cd backend && uv run pytest api/ apps/ -v
```

Expected: PASS（全部）

- [ ] **確認驗收條件**

1. Supervisor 能開關議題（分角色），一般使用者的議題清單跟著變。
2. Supervisor 能調整每個議題的分流門檻，新填的問卷立刻吃到新門檻，**不需重啟服務**。
3. 改門檻後，既有 `UserStanceProfile.stance_category` 不變，既有對話顯示的立場分類也不變。
4. Supervisor 能設定入口模式與配對逾時分鐘數（值已存下，**行為在階段二才生效**）。
5. 非研究者存取三個設定端點一律 403。

階段二（混合入口）見 `docs/superpowers/plans/2026-07-27-mixed-entry-phase-2.md`。
