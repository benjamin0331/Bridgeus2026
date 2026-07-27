# 階段二：混合型對話入口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一般使用者只看到單一對話入口：填完立場問卷後由後端自動分流（中立 → AI 對話、極端 → 真人配對）；配對等太久會詢問是否改跟 AI 對話。研究者維持分開的兩個入口。

**Architecture:** 分流決策與把關全在後端。新增 `DialogueEntryAssignment` 記錄每位使用者在每個議題被分到哪一組，作為把關依據與實驗資料。混合模式下直接呼叫 `/api/matching/join/` 或 `/api/dialogue/sessions/` 會被 403 擋下——`?mode=` 是 query string，不擋就等於讓受試者自己換組。

**Tech Stack:** Django 6 + DRF、pytest、React + Vite。

**依據 spec:** `docs/superpowers/specs/2026-07-27-supervisor-display-settings-and-mixed-entry-design.md`（§5 §7.3 §7.4 §8 §9 §10 §11 §12）

**前置條件：階段一必須先完成**（`docs/superpowers/plans/2026-07-27-display-settings-phase-1.md`）。本階段依賴 `api/display_settings.py` 的 `get_entry_mode()`、`is_topic_visible()`、`get_match_fallback_timeout_seconds()`，以及 `api/permissions.py` 的 `user_is_researcher()`。

---

## 執行前必讀

- 後端指令一律在 `backend/` 目錄下用 `uv run` 執行。
- **測試必須序列執行**：後端測試共用同一個 Postgres test database，同時跑兩個測試程序會失敗。
- 每個 Task 只跑該 Task 的測試檔，最後再跑迴歸。
- 測試指令一律帶絕對路徑 `cd`，否則 `uv run` 找不到虛擬環境並以
  `Failed to spawn: pytest` 靜默失敗（階段一踩過）：
  `cd /Users/light/code/backend && uv run pytest <目標>`

### ⚠️ 已知的測試盲點：`force_authenticate` 會跳過 `authentication_classes`

階段一 Task 5 的教訓。DRF 的 `force_authenticate` 直接塞 `request.user`，**完全不走
authentication 流程**，所以任何只用它的測試都驗證不到「這個 view 用哪個 authentication
class」。階段一因此漏掉了 `DialogueTopicListView` 的核心修正，後來補了一個用真
`AccessToken` 的測試，並以變異測試確認它抓得到退化。

本階段 Task 7（把關）與 Task 8（`/api/me/`）都依賴
`get_entry_mode(is_researcher=user_is_researcher(user))`，而 `user_is_researcher()` 對
simplejwt 的 `TokenUser` **永遠回 False**（`TokenUser.groups` 是 `EmptyManager`）。
這兩個 task 各自**必須加一個用真 JWT 的測試**：

```python
        from rest_framework_simplejwt.tokens import AccessToken

        token = str(AccessToken.for_user(self.researcher))
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
```

並且要實際驗證該測試在退回 stateless auth 時會失敗——否則它只是裝飾。

---

## File Structure

| 檔案 | 責任 |
|---|---|
| `backend/api/models.py`（修改） | 新增 `DialogueEntryAssignment` |
| `backend/api/migrations/0020_*.py`（新增） | 上述 model 的 migration |
| `backend/apps/matching/services/matcher.py`（修改） | 公開 `can_enter_human_matching()` |
| `backend/api/views.py`（修改） | 抽出 AI session 建立、分流端點、fallback 端點、把關、`/api/me/` |
| `backend/api/serializers.py`（修改） | 分流端點輸入驗證、`fallback_offer` 欄位 |
| `backend/api/urls.py`（修改） | 三個新路由 |
| `backend/api/admin.py`（修改） | 註冊 `DialogueEntryAssignment` |
| `backend/api/tests_mixed_entry.py`（新增） | 分流、fallback、把關的測試 |
| `frontend/src/App.jsx`（修改） | 取得並下傳 `entryMode` |
| `frontend/src/components/IssueCard.jsx`（修改） | 混合模式渲染單張卡 |
| `frontend/src/pages/TopicChat.jsx`（修改） | 混合流程 + fallback 詢問對話框 |

---

## Task 1: `DialogueEntryAssignment` model

**Files:**
- Modify: `backend/api/models.py`（檔尾追加）
- Create: `backend/api/migrations/0020_dialogueentryassignment.py`（由 makemigrations 產生）
- Test: `backend/api/tests_mixed_entry.py`

- [ ] **Step 1: 寫失敗的測試**

建立 `backend/api/tests_mixed_entry.py`：

```python
"""混合型對話入口：分流、逾時 fallback、後端把關。

一般使用者只有一個入口，由後端依立場分流；直接呼叫 join/sessions 會被擋。
見 docs/superpowers/specs/2026-07-27-supervisor-display-settings-and-mixed-entry-design.md
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from api.models import DialogueEntryAssignment
from api.permissions import RESEARCHER_GROUP_NAME

User = get_user_model()


def make_researcher(username):
    group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
    user = User.objects.create_user(username=username, password="pw-strong-12345")
    user.groups.add(group)
    return user


class DialogueEntryAssignmentModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="assign_owner", password="pw-strong-12345"
        )

    def _create(self, **overrides):
        defaults = {
            "user": self.user,
            "topic_id": 102,
            "route": DialogueEntryAssignment.Route.MATCH,
            "stance_score": "6.00",
            "stance_category": "support",
            "support_threshold": 4.5,
            "oppose_threshold": 3.5,
            "entry_mode_at_assignment": "mixed",
        }
        defaults.update(overrides)
        return DialogueEntryAssignment.objects.create(**defaults)

    def test_fallback_fields_start_empty(self):
        assignment = self._create()

        self.assertIsNone(assignment.fallback_offered_at)
        self.assertIsNone(assignment.fallback_accepted_at)
        self.assertIsNotNone(assignment.assigned_at)

    def test_one_assignment_per_user_and_topic(self):
        from django.db import IntegrityError

        self._create()

        with self.assertRaises(IntegrityError):
            self._create()

    def test_same_user_can_have_assignment_per_topic(self):
        self._create(topic_id=102)
        self._create(topic_id=103)

        self.assertEqual(
            DialogueEntryAssignment.objects.filter(user=self.user).count(), 2
        )
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd backend && uv run pytest api/tests_mixed_entry.py -v
```

Expected: FAIL — `ImportError: cannot import name 'DialogueEntryAssignment' from 'api.models'`

- [ ] **Step 3: 新增 model**

在 `backend/api/models.py` **檔尾**追加：

```python
class DialogueEntryAssignment(models.Model):
    """混合入口把某位使用者在某個議題分到哪一組。

    兩個用途：
    1. 把關依據——混合模式下，直接呼叫 matching/join 或 dialogue/sessions
       要有對應的指派才放行。純推導做不到，因為配對逾時後極端立場的人
       也必須能進 AI。
    2. 實驗資料——「這位受試者被指派到哪組、當時 stance 多少、用哪組門檻
       算的、有沒有因為配對逾時而轉去 AI」。

    重新填寫問卷會覆寫同一筆（update_or_create）並清空 fallback 欄位。
    """

    class Route(models.TextChoices):
        AI = "ai", "AI 對話"
        MATCH = "match", "真人配對"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="entry_assignments",
    )
    topic_id = models.PositiveIntegerField(db_index=True)
    route = models.CharField(max_length=8, choices=Route.choices, db_index=True)
    stance_score = models.DecimalField(max_digits=4, decimal_places=2)
    stance_category = models.CharField(max_length=20)
    # 指派當下生效的門檻。門檻可被 Supervisor 調整，沒有這兩欄就無法回答
    # 「這筆樣本是用哪組門檻分流的」。
    support_threshold = models.FloatField()
    oppose_threshold = models.FloatField()
    entry_mode_at_assignment = models.CharField(max_length=16)
    assigned_at = models.DateTimeField(auto_now_add=True)
    fallback_offered_at = models.DateTimeField(
        null=True, blank=True, help_text="第一次被提示可以改跟 AI 對話的時間"
    )
    fallback_accepted_at = models.DateTimeField(
        null=True, blank=True, help_text="使用者接受改跟 AI 對話的時間"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "topic_id"],
                name="uniq_entry_assignment_user_topic",
            ),
        ]

    def __str__(self):
        return f"user={self.user_id} topic={self.topic_id} route={self.route}"
```

- [ ] **Step 4: 產生 migration**

```bash
cd backend && uv run python manage.py makemigrations api
```

Expected: 產生 `api/migrations/0020_dialogueentryassignment.py`

- [ ] **Step 5: 執行測試確認通過**

```bash
cd backend && uv run pytest api/tests_mixed_entry.py -v
```

Expected: PASS（3 個測試）

- [ ] **Step 6: 註冊到 admin**

在 `backend/api/admin.py` 的 import 區補上 `DialogueEntryAssignment,`，並在檔尾追加：

```python
@admin.register(DialogueEntryAssignment)
class DialogueEntryAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "topic_id",
        "route",
        "stance_category",
        "stance_score",
        "fallback_accepted_at",
        "assigned_at",
    )
    list_filter = ("route", "stance_category", "topic_id")
    search_fields = ("user__username",)
```

- [ ] **Step 7: Commit**

```bash
git add backend/api/models.py backend/api/migrations/0020_dialogueentryassignment.py backend/api/admin.py backend/api/tests_mixed_entry.py
git commit -m "feat(m3): add dialogue entry assignment model"
```

---

## Task 2: 公開分流規則 `can_enter_human_matching()`

**Files:**
- Modify: `backend/apps/matching/services/matcher.py:67-68`
- Test: `backend/api/tests_mixed_entry.py`

混合入口要決定「這個立場該去 AI 還是配對」，用的必須跟佇列內部完全同一份規則，否則兩邊會長歪。`_can_enter_human_matching` 目前是私有的，加一個公開名稱指向同一份實作。

- [ ] **Step 1: 寫失敗的測試**

在 `backend/api/tests_mixed_entry.py` 追加：

```python
class CanEnterHumanMatchingTests(TestCase):
    def test_neutral_cannot_enter_matching(self):
        from apps.matching.services.matcher import can_enter_human_matching

        self.assertFalse(can_enter_human_matching("neutral"))

    def test_support_and_oppose_can_enter_matching(self):
        from apps.matching.services.matcher import can_enter_human_matching

        self.assertTrue(can_enter_human_matching("support"))
        self.assertTrue(can_enter_human_matching("oppose"))

    def test_public_and_private_agree(self):
        from apps.matching.services.matcher import (
            _can_enter_human_matching,
            can_enter_human_matching,
        )

        for category in ("support", "neutral", "oppose"):
            self.assertEqual(
                can_enter_human_matching(category),
                _can_enter_human_matching(category),
            )
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd backend && uv run pytest api/tests_mixed_entry.py::CanEnterHumanMatchingTests -v
```

Expected: FAIL — `ImportError: cannot import name 'can_enter_human_matching'`

- [ ] **Step 3: 實作**

在 `backend/apps/matching/services/matcher.py` 的 `_can_enter_human_matching` 定義之後（約 `:69`）加入：

```python
def can_enter_human_matching(stance_category: str) -> bool:
    """公開版本，給混合入口決定分流方向用。

    刻意包一層而不是直接把 _can_enter_human_matching 改名：佇列內部已有
    多處呼叫，而分流規則必須只有一份定義——兩邊分歧的話，會出現「入口說
    你該配對、佇列說你不能配對」的死路。
    """
    return _can_enter_human_matching(stance_category)
```

- [ ] **Step 4: 執行測試確認通過**

```bash
cd backend && uv run pytest api/tests_mixed_entry.py::CanEnterHumanMatchingTests -v
```

Expected: PASS（3 個測試）

- [ ] **Step 5: Commit**

```bash
git add backend/apps/matching/services/matcher.py backend/api/tests_mixed_entry.py
git commit -m "refactor(m3): expose can_enter_human_matching for mixed entry"
```

---

## Task 3: 抽出 AI session 建立邏輯

**Files:**
- Modify: `backend/api/views.py:942-1024`（`DialogueSessionCreateView`）
- Test: `backend/api/tests_mixed_entry.py`

分流端點與 fallback 端點都要建立 AI session。把邏輯從 view 抽成函式，三個地方共用一份，避免複製貼上。

- [ ] **Step 1: 寫失敗的測試**

在 `backend/api/tests_mixed_entry.py` 追加：

```python
class CreateAiDialogueSessionHelperTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="ai_session_owner", password="pw-strong-12345"
        )

    def test_creates_session_and_profile(self):
        from api.models import DialogueSessionRecord, UserStanceProfile
        from api.views import _create_ai_dialogue_session

        payload = _create_ai_dialogue_session(
            user=self.user,
            topic_id=102,
            survey_answers={str(i): 4 for i in range(1, 9)},
            survey_open_answers={"Q9": "我覺得需要更多討論。", "Q10": "對方會說安全。"},
        )

        self.assertIn("session_id", payload)
        self.assertEqual(payload["stance_category"], "neutral")
        self.assertTrue(
            DialogueSessionRecord.objects.filter(
                user=self.user, session_id=payload["session_id"]
            ).exists()
        )
        self.assertTrue(
            UserStanceProfile.objects.filter(user=self.user, topic_id=102).exists()
        )

    def test_topic_metadata_comes_from_topic_configs(self):
        from api.dialogue_topics import TOPIC_CONFIGS
        from api.models import DialogueSessionRecord
        from api.views import _create_ai_dialogue_session

        payload = _create_ai_dialogue_session(
            user=self.user,
            topic_id=102,
            survey_answers={str(i): 4 for i in range(1, 9)},
            survey_open_answers={"Q9": "我覺得需要更多討論。"},
        )

        record = DialogueSessionRecord.objects.get(session_id=payload["session_id"])
        self.assertEqual(record.topic_id, 102)
        self.assertEqual(
            DialogueSessionRecord.objects.get(
                session_id=payload["session_id"]
            ).topic_id,
            102,
        )
        self.assertIn("核能", TOPIC_CONFIGS[102]["title"])
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd backend && uv run pytest api/tests_mixed_entry.py::CreateAiDialogueSessionHelperTests -v
```

Expected: FAIL — `ImportError: cannot import name '_create_ai_dialogue_session' from 'api.views'`

- [ ] **Step 3: 抽出函式**

在 `backend/api/views.py` 的 `class DialogueSessionCreateView` **之前**插入：

```python
def _create_ai_dialogue_session(
    *,
    user,
    topic_id: int,
    survey_answers: dict,
    survey_open_answers: dict,
    topic_title: str | None = None,
    topic_description: str | None = None,
    user_initial_argument: str | None = None,
) -> dict:
    """建立一場 AI 對話 session，回傳 API 回應用的 payload。

    分流端點、fallback 端點與 DialogueSessionCreateView 共用這一份。
    topic_title / topic_description / user_initial_argument 沒給時一律由後端
    從 TOPIC_CONFIGS 與問卷 Q9 補齊——混合入口不接受客戶端送這些欄位。
    """
    _, _, DialogueSession = _get_dialogue_runtime()

    topic_meta = TOPIC_CONFIGS.get(topic_id, {})
    resolved_open_answers = _resolve_open_answers(
        topic_id=topic_id,
        survey_open_answers=survey_open_answers,
    )
    if user_initial_argument is None:
        user_initial_argument = resolved_open_answers.get("Q9", "")

    topic_config = _build_topic_config(
        topic_id=topic_id,
        topic_title=topic_title or topic_meta.get("title", ""),
        topic_description=(
            topic_description
            if topic_description is not None
            else topic_meta.get("topic_description", "")
        ),
        survey_answers=survey_answers,
        survey_open_answers=survey_open_answers,
        user_initial_argument=user_initial_argument,
    )

    # 只有問卷真的填了才寫 profile，避免用空答案的中立預設值蓋掉真實立場。
    if survey_answers:
        _upsert_user_stance_profile(
            user=user,
            topic_id=topic_id,
            survey_answers=survey_answers,
            survey_open_answers=topic_config["survey_open_answers"],
            user_stance_score=topic_config["user_stance_score"],
            q9_embedding=topic_config["q9_embedding"],
        )

    session = DialogueSession(
        topic=topic_config["topic"],
        topic_description=topic_config["topic_description"],
        agent_stance=topic_config["agent_stance"],
        agent_stance_summary=topic_config["agent_stance_summary"],
        user_stance_label=topic_config["user_stance_label"],
        user_stance_score=topic_config["user_stance_score"],
        user_initial_argument=topic_config["user_initial_argument"],
        user_reasoning_mode=topic_config["user_reasoning_mode"],
    )

    session_id = uuid4().hex
    session_record = {
        "user_id": user.id,
        "session_id": session_id,
        "topic_id": topic_id,
        "topic_title": topic_config["topic"],
        "collection_name": topic_config["collection_name"],
        "survey_context": {
            "survey_answers": survey_answers,
            "survey_open_answers": topic_config["survey_open_answers"],
            "semantic_vector_interface": topic_config["semantic_vector_interface"],
            "q9_embedding": topic_config["q9_embedding"],
        },
        "session": session.to_dict(),
    }
    _cache_dialogue_session_record(session_record)
    _persist_dialogue_session_record(session_record)
    _close_superseded_dialogue_sessions(
        user_id=user.id,
        topic_id=topic_id,
        keep_session_id=session_id,
    )

    return {
        "session_id": session_id,
        "dialogue_phase": session.dialogue_phase.value,
        "stance_score": session.user_stance_score,
        "stance_category": _resolve_stance_category(
            topic_id=topic_id,
            user_stance_score=session.user_stance_score,
        ),
        "stance_label": session.user_stance_label,
        "stance_drift": None,
        "history": session.to_dict()["history"],
    }
```

把 `DialogueSessionCreateView.post` 整個 body 換成：

```python
    def post(self, request):
        serializer = DialogueSessionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        payload = _create_ai_dialogue_session(
            user=request.user,
            topic_id=validated["topic_id"],
            survey_answers=validated.get("survey_answers", {}),
            survey_open_answers=validated.get("survey_open_answers", {}),
            topic_title=validated["topic_title"],
            topic_description=validated.get("topic_description", ""),
            user_initial_argument=validated.get("user_initial_argument", ""),
        )
        return Response(payload, status=status.HTTP_201_CREATED)
```

- [ ] **Step 4: 執行測試確認通過**

```bash
cd backend && uv run pytest api/tests_mixed_entry.py::CreateAiDialogueSessionHelperTests -v
```

Expected: PASS（2 個測試）

- [ ] **Step 5: 確認既有 session 測試沒被弄壞**

```bash
cd backend && uv run pytest api/tests.py api/tests_session_cleanup.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/api/views.py backend/api/tests_mixed_entry.py
git commit -m "refactor(m3): extract reusable ai dialogue session creation"
```

---

## Task 4: 分流端點 `POST /api/dialogue/entry/`

**Files:**
- Modify: `backend/api/serializers.py`（檔尾追加）
- Modify: `backend/api/views.py`（`DialogueSessionCreateView` 之後追加）
- Modify: `backend/api/urls.py`
- Test: `backend/api/tests_mixed_entry.py`

- [ ] **Step 1: 寫失敗的測試**

在 `backend/api/tests_mixed_entry.py` 追加：

```python
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import TopicDisplayOverride

# 議題 102 的反向題是 Q2/Q4/Q5/Q6（SURVEY_CONFIGS 的 reverse_question_ids）。
# 全部答 4 → 反轉後仍是 4 → 平均 4.0 → neutral。
NEUTRAL_ANSWERS = {str(i): 4 for i in range(1, 9)}
# 正向題答 7、反向題答 1 → 反轉後也是 7 → 平均 7.0 → support。
SUPPORT_ANSWERS = {"1": 7, "2": 1, "3": 7, "4": 1, "5": 1, "6": 1, "7": 7, "8": 7}
OPEN_ANSWERS = {"Q9": "我認為需要更多公共討論才能決定。", "Q10": "對方會強調供電穩定。"}


class DialogueEntryRoutingTests(APITestCase):
    def setUp(self):
        self.participant = User.objects.create_user(
            username="entry_participant", password="pw-strong-12345"
        )
        self.client.force_authenticate(user=self.participant)

    def test_neutral_stance_routes_to_ai(self):
        response = self.client.post(
            "/api/dialogue/entry/",
            {
                "topic_id": 102,
                "survey_answers": NEUTRAL_ANSWERS,
                "survey_open_answers": OPEN_ANSWERS,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["route"], "ai")
        self.assertIn("session_id", response.data)

        assignment = DialogueEntryAssignment.objects.get(
            user=self.participant, topic_id=102
        )
        self.assertEqual(assignment.route, DialogueEntryAssignment.Route.AI)
        self.assertEqual(assignment.stance_category, "neutral")
        self.assertEqual(assignment.entry_mode_at_assignment, "mixed")

    def test_extreme_stance_routes_to_match(self):
        response = self.client.post(
            "/api/dialogue/entry/",
            {
                "topic_id": 102,
                "survey_answers": SUPPORT_ANSWERS,
                "survey_open_answers": OPEN_ANSWERS,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["route"], "match")
        self.assertEqual(response.data["status"], "matching")

        assignment = DialogueEntryAssignment.objects.get(
            user=self.participant, topic_id=102
        )
        self.assertEqual(assignment.route, DialogueEntryAssignment.Route.MATCH)
        self.assertEqual(assignment.stance_category, "support")

    def test_assignment_records_thresholds_in_force(self):
        TopicDisplayOverride.objects.create(
            topic_id=102, support_threshold=5.5, oppose_threshold=2.5
        )

        self.client.post(
            "/api/dialogue/entry/",
            {
                "topic_id": 102,
                "survey_answers": NEUTRAL_ANSWERS,
                "survey_open_answers": OPEN_ANSWERS,
            },
            format="json",
        )

        assignment = DialogueEntryAssignment.objects.get(
            user=self.participant, topic_id=102
        )
        self.assertEqual(assignment.support_threshold, 5.5)
        self.assertEqual(assignment.oppose_threshold, 2.5)

    def test_threshold_override_changes_routing(self):
        """預設門檻下 support 走配對；把 support 門檻拉到 7.5 後同樣答案走 AI。"""
        TopicDisplayOverride.objects.create(topic_id=102, support_threshold=7.5)

        response = self.client.post(
            "/api/dialogue/entry/",
            {
                "topic_id": 102,
                "survey_answers": SUPPORT_ANSWERS,
                "survey_open_answers": OPEN_ANSWERS,
            },
            format="json",
        )

        self.assertEqual(response.data["route"], "ai")

    def test_hidden_topic_returns_404(self):
        TopicDisplayOverride.objects.create(
            topic_id=102, visible_to_participant=False
        )

        response = self.client.post(
            "/api/dialogue/entry/",
            {
                "topic_id": 102,
                "survey_answers": NEUTRAL_ANSWERS,
                "survey_open_answers": OPEN_ANSWERS,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(
            DialogueEntryAssignment.objects.filter(user=self.participant).exists()
        )

    def test_resubmitting_survey_overwrites_assignment_and_clears_fallback(self):
        from django.utils import timezone

        self.client.post(
            "/api/dialogue/entry/",
            {
                "topic_id": 102,
                "survey_answers": SUPPORT_ANSWERS,
                "survey_open_answers": OPEN_ANSWERS,
            },
            format="json",
        )
        DialogueEntryAssignment.objects.filter(
            user=self.participant, topic_id=102
        ).update(fallback_offered_at=timezone.now())

        self.client.post(
            "/api/dialogue/entry/",
            {
                "topic_id": 102,
                "survey_answers": NEUTRAL_ANSWERS,
                "survey_open_answers": OPEN_ANSWERS,
            },
            format="json",
        )

        assignment = DialogueEntryAssignment.objects.get(
            user=self.participant, topic_id=102
        )
        self.assertEqual(assignment.route, DialogueEntryAssignment.Route.AI)
        self.assertIsNone(assignment.fallback_offered_at)
        self.assertEqual(
            DialogueEntryAssignment.objects.filter(user=self.participant).count(), 1
        )

    def test_unknown_topic_returns_404(self):
        response = self.client.post(
            "/api/dialogue/entry/",
            {
                "topic_id": 999,
                "survey_answers": NEUTRAL_ANSWERS,
                "survey_open_answers": OPEN_ANSWERS,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd backend && uv run pytest api/tests_mixed_entry.py::DialogueEntryRoutingTests -v
```

Expected: FAIL — 全部 404（路由不存在）

- [ ] **Step 3: 新增 serializer**

在 `backend/api/serializers.py` 檔尾追加：

```python
class DialogueEntrySerializer(serializers.Serializer):
    """混合入口的輸入。

    刻意不收 topic_title / topic_description / user_initial_argument——
    那些一律由後端從 TOPIC_CONFIGS 與問卷 Q9 補齊，少一組可被客戶端
    竄改的輸入。
    """

    topic_id = serializers.IntegerField()
    survey_answers = serializers.DictField(
        child=serializers.IntegerField(min_value=1, max_value=7),
    )
    survey_open_answers = serializers.DictField(
        child=serializers.CharField(
            allow_blank=True,
            trim_whitespace=False,
            max_length=2000,
        ),
        required=False,
    )
```

- [ ] **Step 4: 新增 view**

在 `backend/api/views.py` 的 `DialogueSessionCreateView` 之後追加：

```python
class DialogueEntryView(APIView):
    """一般使用者的唯一對話入口：填完問卷後由後端依立場分流。

    中立 → AI 對話；極端（support／oppose）→ 真人配對。分流規則直接用
    matcher.can_enter_human_matching()，與佇列內部同一份定義。
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from apps.matching.services.matcher import (
            can_enter_human_matching,
            enqueue_for_matching,
        )

        serializer = DialogueEntrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data
        topic_id = validated["topic_id"]

        is_researcher = user_is_researcher(request.user)
        if not is_topic_visible(topic_id=topic_id, is_researcher=is_researcher):
            return Response(
                {"detail": "找不到這個議題。"}, status=status.HTTP_404_NOT_FOUND
            )

        survey_answers = validated["survey_answers"]
        survey_open_answers = validated.get("survey_open_answers", {})

        stance_score = _compute_user_stance_score(
            topic_id=topic_id, survey_answers=survey_answers
        )
        stance_category = _resolve_stance_category(
            topic_id=topic_id, user_stance_score=stance_score
        )
        support_threshold, oppose_threshold = get_stance_thresholds(topic_id=topic_id)

        route = (
            DialogueEntryAssignment.Route.MATCH
            if can_enter_human_matching(stance_category)
            else DialogueEntryAssignment.Route.AI
        )

        DialogueEntryAssignment.objects.update_or_create(
            user=request.user,
            topic_id=topic_id,
            defaults={
                "route": route,
                "stance_score": Decimal(str(stance_score)),
                "stance_category": stance_category,
                "support_threshold": support_threshold,
                "oppose_threshold": oppose_threshold,
                "entry_mode_at_assignment": get_entry_mode(
                    is_researcher=is_researcher
                ),
                # 重填問卷＝重新分流，之前的逾時提示紀錄不再適用。
                "fallback_offered_at": None,
                "fallback_accepted_at": None,
            },
        )

        if route == DialogueEntryAssignment.Route.AI:
            payload = _create_ai_dialogue_session(
                user=request.user,
                topic_id=topic_id,
                survey_answers=survey_answers,
                survey_open_answers=survey_open_answers,
            )
            return Response(
                {"route": "ai", **payload}, status=status.HTTP_201_CREATED
            )

        state = enqueue_for_matching(
            user=request.user,
            topic_id=topic_id,
            stance_score=stance_score,
            stance_category=stance_category,
            survey_answers=survey_answers,
            survey_open_answers=_resolve_open_answers(
                topic_id=topic_id,
                survey_open_answers=survey_open_answers,
            ),
        )
        return Response(
            {
                "route": "match",
                **_build_matching_state_payload(
                    topic_id=topic_id,
                    state=state,
                    user_id=request.user.id,
                ),
            },
            status=status.HTTP_201_CREATED,
        )
```

補齊 `backend/api/views.py` 的 import：

```python
from .display_settings import (
    default_stance_thresholds,
    get_entry_mode,
    get_match_fallback_timeout_seconds,
    get_stance_thresholds,
    is_topic_visible,
    visible_topics,
)
```

`from .models import (...)` 補上 `DialogueEntryAssignment,`。
`from .serializers import (...)` 補上 `DialogueEntrySerializer,`。

- [ ] **Step 5: 新增路由**

在 `backend/api/urls.py` 的 `path('dialogue/sessions/', ...)` **之前**插入：

```python
    path('dialogue/entry/', views.DialogueEntryView.as_view()),
```

- [ ] **Step 6: 執行測試確認通過**

```bash
cd backend && uv run pytest api/tests_mixed_entry.py::DialogueEntryRoutingTests -v
```

Expected: PASS（7 個測試）

- [ ] **Step 7: Commit**

```bash
git add backend/api/serializers.py backend/api/views.py backend/api/urls.py backend/api/tests_mixed_entry.py
git commit -m "feat(m3): add mixed dialogue entry routing endpoint"
```

---

## Task 5: 配對逾時提示

**Files:**
- Modify: `backend/api/serializers.py:206-230`（`MatchingStateSerializer`）
- Modify: `backend/api/views.py:553-585`（`_build_matching_state_payload`）
- Test: `backend/api/tests_mixed_entry.py`

- [ ] **Step 1: 寫失敗的測試**

在 `backend/api/tests_mixed_entry.py` 追加：

```python
class FallbackOfferTests(APITestCase):
    def setUp(self):
        self.participant = User.objects.create_user(
            username="fallback_participant", password="pw-strong-12345"
        )
        self.client.force_authenticate(user=self.participant)
        self.client.post(
            "/api/dialogue/entry/",
            {
                "topic_id": 102,
                "survey_answers": SUPPORT_ANSWERS,
                "survey_open_answers": OPEN_ANSWERS,
            },
            format="json",
        )

    def _age_queue_entry(self, seconds):
        from datetime import timedelta

        from django.utils import timezone

        from api.models import MatchQueueEntry

        MatchQueueEntry.objects.filter(
            user=self.participant, topic_id=102
        ).update(waiting_started_at=timezone.now() - timedelta(seconds=seconds))

    def test_offer_unavailable_before_timeout(self):
        response = self.client.get("/api/matching/status/?topic_id=102")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["fallback_offer"]["available"])
        self.assertEqual(response.data["fallback_offer"]["timeout_seconds"], 300)

    def test_offer_available_after_timeout_and_records_time(self):
        self._age_queue_entry(400)

        response = self.client.get("/api/matching/status/?topic_id=102")

        self.assertTrue(response.data["fallback_offer"]["available"])
        self.assertGreaterEqual(response.data["fallback_offer"]["waited_seconds"], 400)

        assignment = DialogueEntryAssignment.objects.get(
            user=self.participant, topic_id=102
        )
        self.assertIsNotNone(assignment.fallback_offered_at)

    def test_offer_respects_configured_timeout(self):
        from api.models import PlatformDisplaySetting

        setting = PlatformDisplaySetting.load()
        setting.match_fallback_timeout_minutes = 1
        setting.save()

        self._age_queue_entry(90)
        response = self.client.get("/api/matching/status/?topic_id=102")

        self.assertTrue(response.data["fallback_offer"]["available"])
        self.assertEqual(response.data["fallback_offer"]["timeout_seconds"], 60)

    def test_split_mode_gets_no_offer(self):
        from api.models import PlatformDisplaySetting

        setting = PlatformDisplaySetting.load()
        setting.participant_entry_mode = PlatformDisplaySetting.EntryMode.SPLIT
        setting.save()

        self._age_queue_entry(400)
        response = self.client.get("/api/matching/status/?topic_id=102")

        self.assertIsNone(response.data["fallback_offer"])
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd backend && uv run pytest api/tests_mixed_entry.py::FallbackOfferTests -v
```

Expected: FAIL — `KeyError: 'fallback_offer'`

- [ ] **Step 3: 加入 serializer 欄位**

在 `backend/api/serializers.py:230` 的 `absence_deadline` 之後加入：

```python
    fallback_offer = serializers.JSONField(required=False, allow_null=True)
```

- [ ] **Step 4: 實作計算**

在 `backend/api/views.py` 的 `_build_matching_state_payload` **之前**加入：

```python
def _fallback_offer_fields(*, topic_id: int, state, user_id: int) -> dict:
    """配對等太久要不要提示改跟 AI 對話。

    只有混合入口需要這個提示——分開入口的使用者本來就是自己選的模式，
    回傳 None 讓前端不要顯示對話框。
    """
    queue_entry = state.queue_entry
    if (
        queue_entry is None
        or queue_entry.status != MatchQueueEntry.Status.MATCHING
        or queue_entry.waiting_started_at is None
    ):
        return {"fallback_offer": None}

    user = User.objects.filter(pk=user_id).first()
    if user is None:
        return {"fallback_offer": None}

    entry_mode = get_entry_mode(is_researcher=user_is_researcher(user))
    if entry_mode != PlatformDisplaySetting.EntryMode.MIXED:
        return {"fallback_offer": None}

    timeout_seconds = get_match_fallback_timeout_seconds()
    waited_seconds = int(
        (timezone.now() - queue_entry.waiting_started_at).total_seconds()
    )
    available = waited_seconds >= timeout_seconds

    if available:
        # 記錄第一次被提示的時間（研究資料）。實際的 fallback 授權是在
        # fallback 端點當場重算等待時間，不依賴這個欄位。
        DialogueEntryAssignment.objects.filter(
            user_id=user_id,
            topic_id=topic_id,
            fallback_offered_at__isnull=True,
        ).update(fallback_offered_at=timezone.now())

    return {
        "fallback_offer": {
            "available": available,
            "waited_seconds": waited_seconds,
            "timeout_seconds": timeout_seconds,
        }
    }
```

在 `_build_matching_state_payload` 的 `payload = {...}` 內，把 `**_match_presence_fields(match, user_id=user_id),` 之後補上：

```python
        **_fallback_offer_fields(topic_id=topic_id, state=state, user_id=user_id),
```

`from .models import (...)` 補上 `MatchQueueEntry,` 與 `PlatformDisplaySetting,`。

- [ ] **Step 5: 執行測試確認通過**

```bash
cd backend && uv run pytest api/tests_mixed_entry.py::FallbackOfferTests -v
```

Expected: PASS（4 個測試）

- [ ] **Step 6: 確認既有配對測試沒被弄壞**

```bash
cd backend && uv run pytest api/tests.py -k "match or Match" -v
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/api/serializers.py backend/api/views.py backend/api/tests_mixed_entry.py
git commit -m "feat(m3): offer ai fallback when matching wait exceeds timeout"
```

---

## Task 6: fallback 端點 `POST /api/dialogue/entry/fallback/`

**Files:**
- Modify: `backend/api/views.py`（`DialogueEntryView` 之後追加）
- Modify: `backend/api/urls.py`
- Test: `backend/api/tests_mixed_entry.py`

- [ ] **Step 1: 寫失敗的測試**

在 `backend/api/tests_mixed_entry.py` 追加：

```python
class FallbackAcceptTests(APITestCase):
    def setUp(self):
        self.participant = User.objects.create_user(
            username="fallback_accepter", password="pw-strong-12345"
        )
        self.client.force_authenticate(user=self.participant)
        self.client.post(
            "/api/dialogue/entry/",
            {
                "topic_id": 102,
                "survey_answers": SUPPORT_ANSWERS,
                "survey_open_answers": OPEN_ANSWERS,
            },
            format="json",
        )

    def _age_queue_entry(self, seconds):
        from datetime import timedelta

        from django.utils import timezone

        from api.models import MatchQueueEntry

        MatchQueueEntry.objects.filter(
            user=self.participant, topic_id=102
        ).update(waiting_started_at=timezone.now() - timedelta(seconds=seconds))

    def test_accept_creates_ai_session_and_cancels_queue(self):
        from api.models import MatchQueueEntry

        self._age_queue_entry(400)

        response = self.client.post(
            "/api/dialogue/entry/fallback/", {"topic_id": 102}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["route"], "ai")
        self.assertIn("session_id", response.data)

        assignment = DialogueEntryAssignment.objects.get(
            user=self.participant, topic_id=102
        )
        self.assertIsNotNone(assignment.fallback_accepted_at)
        self.assertEqual(assignment.route, DialogueEntryAssignment.Route.MATCH)

        self.assertFalse(
            MatchQueueEntry.objects.filter(
                user=self.participant,
                topic_id=102,
                status=MatchQueueEntry.Status.MATCHING,
            ).exists()
        )

    def test_accept_reuses_stored_survey_answers(self):
        """不能要求受試者為了 fallback 重填一次問卷。"""
        from api.models import DialogueSessionRecord, UserStanceProfile

        self._age_queue_entry(400)
        profile = UserStanceProfile.objects.get(user=self.participant, topic_id=102)

        response = self.client.post(
            "/api/dialogue/entry/fallback/", {"topic_id": 102}, format="json"
        )

        record = DialogueSessionRecord.objects.get(
            session_id=response.data["session_id"]
        )
        self.assertEqual(record.user_id, self.participant.id)
        self.assertEqual(
            UserStanceProfile.objects.get(
                user=self.participant, topic_id=102
            ).survey_answers,
            profile.survey_answers,
        )

    def test_reject_before_timeout(self):
        response = self.client.post(
            "/api/dialogue/entry/fallback/", {"topic_id": 102}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("尚未達到等待時間", response.data["detail"])

    def test_reject_without_assignment(self):
        other = User.objects.create_user(
            username="no_assignment", password="pw-strong-12345"
        )
        self.client.force_authenticate(user=other)

        response = self.client.post(
            "/api/dialogue/entry/fallback/", {"topic_id": 102}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reject_when_route_is_ai(self):
        other = User.objects.create_user(
            username="ai_routed", password="pw-strong-12345"
        )
        self.client.force_authenticate(user=other)
        self.client.post(
            "/api/dialogue/entry/",
            {
                "topic_id": 102,
                "survey_answers": NEUTRAL_ANSWERS,
                "survey_open_answers": OPEN_ANSWERS,
            },
            format="json",
        )

        response = self.client.post(
            "/api/dialogue/entry/fallback/", {"topic_id": 102}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_topic_id(self):
        response = self.client.post(
            "/api/dialogue/entry/fallback/", {"topic_id": "abc"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd backend && uv run pytest api/tests_mixed_entry.py::FallbackAcceptTests -v
```

Expected: FAIL — 全部 404（路由不存在）

- [ ] **Step 3: 實作**

在 `backend/api/views.py` 的 `DialogueEntryView` 之後追加：

```python
class DialogueEntryFallbackView(APIView):
    """配對等太久，使用者同意改跟 AI 對話。

    授權條件當場從 MatchQueueEntry.waiting_started_at 重算，不看
    fallback_offered_at——後者會讓這個端點依賴前端「必須先輪詢過 status」，
    多一個沒必要的隱性順序耦合。
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from apps.matching.services.matcher import cancel_matching

        try:
            topic_id = int(request.data.get("topic_id"))
        except (TypeError, ValueError):
            return Response(
                {"detail": "topic_id 必須是有效的議題編號。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        assignment = DialogueEntryAssignment.objects.filter(
            user=request.user, topic_id=topic_id
        ).first()
        if (
            assignment is None
            or assignment.route != DialogueEntryAssignment.Route.MATCH
        ):
            return Response(
                {"detail": "目前沒有等待中的配對。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        queue_entry = (
            MatchQueueEntry.objects.filter(
                user=request.user,
                topic_id=topic_id,
                status=MatchQueueEntry.Status.MATCHING,
            )
            .order_by("-waiting_started_at", "-id")
            .first()
        )
        if queue_entry is None:
            return Response(
                {"detail": "目前沒有等待中的配對。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        waited_seconds = (
            timezone.now() - queue_entry.waiting_started_at
        ).total_seconds()
        if waited_seconds < get_match_fallback_timeout_seconds():
            return Response(
                {"detail": "尚未達到等待時間。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile = UserStanceProfile.objects.filter(
            user=request.user, topic_id=topic_id
        ).first()
        if profile is None:
            return Response(
                {"detail": "找不到立場問卷紀錄，請重新填寫。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cancel_matching(user=request.user, topic_id=topic_id)

        now = timezone.now()
        assignment.fallback_accepted_at = now
        if assignment.fallback_offered_at is None:
            assignment.fallback_offered_at = now
        assignment.save(
            update_fields=["fallback_offered_at", "fallback_accepted_at"]
        )

        payload = _create_ai_dialogue_session(
            user=request.user,
            topic_id=topic_id,
            survey_answers=profile.survey_answers or {},
            survey_open_answers=profile.survey_open_answers or {},
        )
        return Response({"route": "ai", **payload}, status=status.HTTP_201_CREATED)
```

- [ ] **Step 4: 新增路由**

在 `backend/api/urls.py` 的 `path('dialogue/entry/', ...)` 之後插入：

```python
    path('dialogue/entry/fallback/', views.DialogueEntryFallbackView.as_view()),
```

- [ ] **Step 5: 執行測試確認通過**

```bash
cd backend && uv run pytest api/tests_mixed_entry.py::FallbackAcceptTests -v
```

Expected: PASS（6 個測試）

- [ ] **Step 6: Commit**

```bash
git add backend/api/views.py backend/api/urls.py backend/api/tests_mixed_entry.py
git commit -m "feat(m3): add ai fallback endpoint for timed-out matching"
```

---

## Task 7: 後端把關

**Files:**
- Modify: `backend/api/views.py`（`MatchingJoinView.post`、`DialogueSessionCreateView.post`）
- Test: `backend/api/tests_mixed_entry.py`

- [ ] **Step 1: 寫失敗的測試**

在 `backend/api/tests_mixed_entry.py` 追加：

```python
class EntryGateTests(APITestCase):
    def setUp(self):
        self.participant = User.objects.create_user(
            username="gated_participant", password="pw-strong-12345"
        )
        self.researcher = make_researcher("gated_researcher")

    def _join_payload(self):
        return {
            "topic_id": 102,
            "survey_answers": SUPPORT_ANSWERS,
            "survey_open_answers": OPEN_ANSWERS,
        }

    def _session_payload(self):
        return {
            "topic_id": 102,
            "topic_title": "台灣核能議題討論",
            "survey_answers": NEUTRAL_ANSWERS,
            "survey_open_answers": OPEN_ANSWERS,
        }

    def test_mixed_mode_blocks_direct_join_without_assignment(self):
        self.client.force_authenticate(user=self.participant)

        response = self.client.post(
            "/api/matching/join/", self._join_payload(), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["detail"], "請從議題頁面開始對話。")

    def test_mixed_mode_blocks_direct_session_without_assignment(self):
        self.client.force_authenticate(user=self.participant)

        response = self.client.post(
            "/api/dialogue/sessions/", self._session_payload(), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_mixed_mode_blocks_match_routed_user_from_ai_session(self):
        self.client.force_authenticate(user=self.participant)
        self.client.post(
            "/api/dialogue/entry/", self._join_payload(), format="json"
        )

        response = self.client.post(
            "/api/dialogue/sessions/", self._session_payload(), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_ai_routed_user_may_create_session(self):
        self.client.force_authenticate(user=self.participant)
        self.client.post(
            "/api/dialogue/entry/",
            {
                "topic_id": 102,
                "survey_answers": NEUTRAL_ANSWERS,
                "survey_open_answers": OPEN_ANSWERS,
            },
            format="json",
        )

        response = self.client.post(
            "/api/dialogue/sessions/", self._session_payload(), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_match_routed_user_may_create_session_after_fallback(self):
        from datetime import timedelta

        from django.utils import timezone

        from api.models import MatchQueueEntry

        self.client.force_authenticate(user=self.participant)
        self.client.post(
            "/api/dialogue/entry/", self._join_payload(), format="json"
        )
        MatchQueueEntry.objects.filter(
            user=self.participant, topic_id=102
        ).update(waiting_started_at=timezone.now() - timedelta(seconds=400))
        self.client.post(
            "/api/dialogue/entry/fallback/", {"topic_id": 102}, format="json"
        )

        response = self.client.post(
            "/api/dialogue/sessions/", self._session_payload(), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_match_routed_user_may_call_join(self):
        self.client.force_authenticate(user=self.participant)
        self.client.post(
            "/api/dialogue/entry/", self._join_payload(), format="json"
        )

        response = self.client.post(
            "/api/matching/join/", self._join_payload(), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_researcher_in_split_mode_is_not_gated(self):
        self.client.force_authenticate(user=self.researcher)

        join_response = self.client.post(
            "/api/matching/join/", self._join_payload(), format="json"
        )
        session_response = self.client.post(
            "/api/dialogue/sessions/", self._session_payload(), format="json"
        )

        self.assertEqual(join_response.status_code, status.HTTP_200_OK)
        self.assertEqual(session_response.status_code, status.HTTP_201_CREATED)

    def test_participant_in_split_mode_is_not_gated(self):
        from api.models import PlatformDisplaySetting

        setting = PlatformDisplaySetting.load()
        setting.participant_entry_mode = PlatformDisplaySetting.EntryMode.SPLIT
        setting.save()

        self.client.force_authenticate(user=self.participant)
        response = self.client.post(
            "/api/dialogue/sessions/", self._session_payload(), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd backend && uv run pytest api/tests_mixed_entry.py::EntryGateTests -v
```

Expected: FAIL — 前三個測試得到 200/201 而非 403

- [ ] **Step 3: 實作把關**

在 `backend/api/views.py` 的 `DialogueEntryFallbackView` 之後加入：

```python
ENTRY_GATE_DETAIL = "請從議題頁面開始對話。"


def _entry_gate_response(*, user, topic_id: int, target: str):
    """混合入口下擋掉繞過分流的直接呼叫；回傳 Response 代表擋下，None 代表放行。

    ?mode= 只是 query string，不在後端擋的話受試者改個網址就能自己換組，
    實驗分組就不可信了。訊息刻意不說明分流規則——講了等於告訴受試者
    自己被分到哪一組，會影響後續作答。

    target: "match" 或 "ai"
    """
    if get_entry_mode(is_researcher=user_is_researcher(user)) != (
        PlatformDisplaySetting.EntryMode.MIXED
    ):
        return None

    assignment = DialogueEntryAssignment.objects.filter(
        user=user, topic_id=topic_id
    ).first()
    if assignment is None:
        return Response(
            {"detail": ENTRY_GATE_DETAIL}, status=status.HTTP_403_FORBIDDEN
        )

    if target == "match":
        allowed = assignment.route == DialogueEntryAssignment.Route.MATCH
    else:
        allowed = (
            assignment.route == DialogueEntryAssignment.Route.AI
            or assignment.fallback_accepted_at is not None
        )

    if allowed:
        return None
    return Response({"detail": ENTRY_GATE_DETAIL}, status=status.HTTP_403_FORBIDDEN)
```

在 `MatchingJoinView.post` 的 `validated = serializer.validated_data` 之後插入：

```python
        gate = _entry_gate_response(
            user=request.user, topic_id=validated["topic_id"], target="match"
        )
        if gate is not None:
            return gate
```

在 `DialogueSessionCreateView.post` 的 `validated = serializer.validated_data` 之後插入：

```python
        gate = _entry_gate_response(
            user=request.user, topic_id=validated["topic_id"], target="ai"
        )
        if gate is not None:
            return gate
```

> `DialogueEntryView` 與 `DialogueEntryFallbackView` 直接呼叫服務層
> （`enqueue_for_matching` / `_create_ai_dialogue_session`）而不是這兩個 view，
> 所以不會被自己的把關擋住。

- [ ] **Step 4: 執行測試確認通過**

```bash
cd backend && uv run pytest api/tests_mixed_entry.py::EntryGateTests -v
```

Expected: PASS（8 個測試）

- [ ] **Step 5: 確認既有測試沒被弄壞**

```bash
cd backend && uv run pytest api/ apps/ -v
```

Expected: PASS（全部；此步驟較慢，約 5–10 分鐘）

> 若既有測試因把關而失敗（例如某個測試直接打 `/api/dialogue/sessions/` 而沒先分流），
> 修法是在該測試的 setUp 把 `PlatformDisplaySetting.participant_entry_mode` 設成
> `split`，或改走 `/api/dialogue/entry/`——**不要**放寬把關邏輯。

- [ ] **Step 6: Commit**

```bash
git add backend/api/views.py backend/api/tests_mixed_entry.py
git commit -m "feat(m3): enforce entry assignment in mixed mode

?mode= 只是 query string，不在後端擋的話受試者改網址就能自己換組。"
```

---

## Task 8: `GET /api/me/` 提供入口模式

**Files:**
- Modify: `backend/api/views.py`
- Modify: `backend/api/urls.py`
- Test: `backend/api/tests_mixed_entry.py`

前端需要知道自己的入口模式，但一般使用者讀不到 `/api/settings/display/`（那是 `IsResearcher`）。也**不能**從 JWT 的 `is_researcher` claim 讀——那個 claim 在降級後仍會存活到 refresh token 過期（最長 7 天）。

- [ ] **Step 1: 寫失敗的測試**

在 `backend/api/tests_mixed_entry.py` 追加：

```python
class MeEndpointTests(APITestCase):
    def test_participant_sees_mixed_entry_mode(self):
        participant = User.objects.create_user(
            username="me_participant", password="pw-strong-12345"
        )
        self.client.force_authenticate(user=participant)

        response = self.client.get("/api/me/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], participant.id)
        self.assertEqual(response.data["username"], "me_participant")
        self.assertFalse(response.data["is_researcher"])
        self.assertEqual(response.data["entry_mode"], "mixed")

    def test_researcher_sees_split_entry_mode(self):
        researcher = make_researcher("me_researcher")
        self.client.force_authenticate(user=researcher)

        response = self.client.get("/api/me/")

        self.assertTrue(response.data["is_researcher"])
        self.assertEqual(response.data["entry_mode"], "split")

    def test_entry_mode_follows_current_setting_not_token(self):
        from api.models import PlatformDisplaySetting

        participant = User.objects.create_user(
            username="me_switcher", password="pw-strong-12345"
        )
        setting = PlatformDisplaySetting.load()
        setting.participant_entry_mode = PlatformDisplaySetting.EntryMode.SPLIT
        setting.save()

        self.client.force_authenticate(user=participant)
        response = self.client.get("/api/me/")

        self.assertEqual(response.data["entry_mode"], "split")

    def test_requires_authentication(self):
        response = self.client.get("/api/me/")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd backend && uv run pytest api/tests_mixed_entry.py::MeEndpointTests -v
```

Expected: FAIL — 404（路由不存在）

- [ ] **Step 3: 實作**

在 `backend/api/views.py` 的 `_entry_gate_response` 之後加入：

```python
class MeView(APIView):
    """目前登入者的即時身分與入口模式。

    前端不從 JWT 的 is_researcher claim 讀這些：那個 claim 是簽發當下的快照，
    使用者被降級後仍會隨著 refresh token 存活最長 7 天。這裡每次都查 DB。
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        is_researcher = user_is_researcher(request.user)
        return Response(
            {
                "id": request.user.id,
                "username": request.user.username,
                "is_researcher": is_researcher,
                "entry_mode": get_entry_mode(is_researcher=is_researcher),
            }
        )
```

在 `backend/api/urls.py` 的 `path('titles/me/', ...)` **之前**插入：

```python
    path('me/', views.MeView.as_view()),
```

- [ ] **Step 4: 執行測試確認通過**

```bash
cd backend && uv run pytest api/tests_mixed_entry.py::MeEndpointTests -v
```

Expected: PASS（4 個測試）

- [ ] **Step 5: Commit**

```bash
git add backend/api/views.py backend/api/urls.py backend/api/tests_mixed_entry.py
git commit -m "feat(m1): add /api/me/ with live role and entry mode"
```

---

## Task 9: 前端混合入口

**Files:**
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/components/IssueCard.jsx`
- Modify: `frontend/src/pages/TopicChat.jsx`

前端無自動化測試，以手動驗收為準。

- [ ] **Step 1: `App.jsx` 取得 entryMode**

在 `App.jsx` 的 `const [issuesLoaded, setIssuesLoaded] = useState(false);` 之後加入：

```jsx
  const [entryMode, setEntryMode] = useState('split');
```

在既有的 `fetchIssuesFromBackend` effect **之後**新增一個 effect：

```jsx
  useEffect(() => {
    if (!user) {
      setEntryMode('split');
      return undefined;
    }

    let cancelled = false;

    const fetchEntryMode = async () => {
      try {
        const response = await api.get('/api/me/');
        if (!cancelled) {
          setEntryMode(response.data?.entry_mode === 'mixed' ? 'mixed' : 'split');
        }
      } catch (error) {
        console.error('Failed to load entry mode:', error);
        // 讀不到就退回分開入口：兩個入口都看得到比整個消失好。
        if (!cancelled) setEntryMode('split');
      }
    };

    void fetchEntryMode();

    return () => {
      cancelled = true;
    };
  }, [user]);
```

把 `HomePage` 的 props 加上 `entryMode={entryMode}`。

- [ ] **Step 2: `HomePage` 下傳給 `IssueCard`**

把 `frontend/src/pages/HomePage.jsx:5` 的簽章改為：

```jsx
function HomePage({ navigate, userName, issues, issuesLoaded, entryMode }) {
```

把 `frontend/src/pages/HomePage.jsx:15` 改為：

```jsx
                <IssueCard
                    navigate={navigate}
                    issues={issues}
                    issuesLoaded={issuesLoaded}
                    entryMode={entryMode}
                />
```

- [ ] **Step 3: `IssueCard.jsx` 依模式渲染**

把 `frontend/src/components/IssueCard.jsx` 的 `buildIssueEntries` 與元件簽章改為：

```jsx
function buildIssueEntries(issues, entryMode) {
  // 混合入口：每個議題只有一張卡，導向不帶 mode 的路徑，由後端依立場分流。
  if (entryMode === 'mixed') {
    return issues.map((issue) => ({
      ...issue,
      entryKey: `${issue.id}-mixed`,
      mode: null,
      modeLabel: '開始對話',
      modeDescription: '填完立場問卷後自動安排對談對象',
    }));
  }

  return issues.flatMap((issue) =>
    ISSUE_ENTRY_MODES.map((mode) => ({
      ...issue,
      entryKey: `${issue.id}-${mode.key}`,
      mode: mode.key,
      modeLabel: mode.label,
      modeDescription: mode.description,
    })),
  );
}

function IssueCard({ navigate, issues, issuesLoaded, entryMode = 'split' }) {
  const entries = buildIssueEntries(issues, entryMode);
```

把 `onClick` 改為：

```jsx
              onClick={() =>
                navigate(
                  issue.mode ? `/topic/${issue.id}?mode=${issue.mode}` : `/topic/${issue.id}`,
                )
              }
```

把 badge 的 className 改為 `className={`issue-mode-badge mode-${issue.mode || 'mixed'}`}`。

- [ ] **Step 4: `TopicChat.jsx` 混合流程**

把 `TopicChat.jsx:212-217` 的 mode 判斷改為：

```jsx
  const mode = useMemo(() => {
    const params = new URLSearchParams(location.search);
    const raw = params.get('mode');
    if (raw === 'match') return 'match';
    if (raw === 'ai') return 'ai';
    // 沒帶 mode＝混合入口：先填問卷，由後端分流後才知道是哪一種。
    return 'mixed';
  }, [location.search]);
  const [resolvedMode, setResolvedMode] = useState(mode === 'mixed' ? null : mode);
  const isMixedEntry = mode === 'mixed';
  const isMatchingMode = (resolvedMode ?? mode) === 'match';
  const modeLabel = isMatchingMode ? '配對模式' : 'AI 模式';
```

把 `TopicChat.jsx:219` 的 `showSurvey` 初值改為：

```jsx
  const [showSurvey, setShowSurvey] = useState(isMixedEntry || !isMatchingMode);
```

在 `handleSurveySubmit`（`TopicChat.jsx:1422`）中，於 `setSavedStanceProfile({...})` 區塊之後、`if (!isMatchingMode) {` **之前**插入混合分支：

```jsx
    if (isMixedEntry) {
      setMatchingError('');
      setIsMatchingActionLoading(true);
      try {
        const response = await api.post('/api/dialogue/entry/', {
          topic_id: Number(id),
          survey_answers: answers,
          survey_open_answers: openAnswers,
        });

        if (!isChatPageMountedRef.current) return;

        if (response.data.route === 'ai') {
          setResolvedMode('ai');
          // 分流端點已經把 session 建好了，所以直接收下 session_id——
          // ensureSession() 之後會因為 sessionId 有值而短路，不會重建一場。
          setSessionId(response.data.session_id);
          setAiStanceMeta(extractAiStanceMeta(response.data));
          setAiStanceDrift(extractStanceDrift(response.data));
          setMessages([]);
          setSemanticTreePayload(null);
          setSemanticTreeStatus('ready');
          setSemanticTreeMessage('');
          semanticTreeAnalyzeSignatureRef.current = '';
          shouldAutoScrollAiRef.current = true;
          setChatError('');
        } else {
          setResolvedMode('match');
          setMatchingState(response.data);
        }
        setShowSurvey(false);
      } catch (error) {
        if (!isChatPageMountedRef.current) return;
        setMatchingError(
          error?.response?.data?.detail || '目前無法開始對話，請稍後再試。',
        );
      } finally {
        if (isChatPageMountedRef.current) {
          setIsMatchingActionLoading(false);
        }
      }
      return;
    }
```

> 兩個實作重點：
> 1. 這個分支必須排在 `if (!isMatchingMode)` **之前**。混合入口在問卷送出當下
>    `resolvedMode` 還是 null、`mode` 是 `'mixed'`，`isMatchingMode` 會是 false，
>    排在後面的話會走進 AI 分支。
> 2. AI 路徑刻意**不**呼叫 `ensureSession()`。既有的 AI 流程是延遲建立 session
>    （第一次送訊息時才 POST `/api/dialogue/sessions/`），但混合入口的 session 已由
>    分流端點建好；而且此時直接打 `/api/dialogue/sessions/` 會被 Task 7 的把關擋下。

- [ ] **Step 5: fallback 詢問對話框**

在 `TopicChat.jsx` 加入狀態與處理：

```jsx
  const [fallbackBusy, setFallbackBusy] = useState(false);
  const fallbackOffer = matchingState?.fallback_offer;

  const handleAcceptFallback = async () => {
    if (fallbackBusy) return;
    setFallbackBusy(true);
    try {
      const response = await api.post('/api/dialogue/entry/fallback/', {
        topic_id: Number(id),
      });
      setResolvedMode('ai');
      setMatchingState(null);
      setSessionId(response.data.session_id);
      setAiStanceMeta(extractAiStanceMeta(response.data));
      setAiStanceDrift(extractStanceDrift(response.data));
      setMessages([]);
      shouldAutoScrollAiRef.current = true;
    } catch (error) {
      setChatError(
        error?.response?.data?.detail || '目前無法改成 AI 對話，請稍後再試。',
      );
    } finally {
      setFallbackBusy(false);
    }
  };
```

在配對等待畫面（`matchingStatus === 'matching'` 的分支）內、等待說明文字之後插入：

```jsx
              {fallbackOffer?.available && (
                <div className="matching-fallback-offer">
                  <p>目前沒有找到合適的對談對象。要改成和 AI 代理人對話嗎？</p>
                  <div className="matching-status-actions">
                    <button
                      className="matching-status-btn"
                      type="button"
                      disabled={fallbackBusy}
                      onClick={handleAcceptFallback}
                    >
                      改成 AI 對話
                    </button>
                    <button
                      className="matching-status-btn secondary"
                      type="button"
                      disabled={fallbackBusy}
                      onClick={() => setMatchingState((prev) => (
                        prev ? { ...prev, fallback_offer: null } : prev
                      ))}
                    >
                      繼續等待
                    </button>
                  </div>
                </div>
              )}
```

在 `frontend/src/pages/TopicChat.css` 檔尾追加：

```css
.matching-fallback-offer {
  margin-top: 20px;
  padding: 16px;
  border: 1px solid #f0d9a0;
  border-radius: 10px;
  background: #fff9ec;
}

.matching-fallback-offer p {
  margin: 0 0 12px;
  font-size: 14px;
  color: #8a6d3b;
}
```

- [ ] **Step 6: 手動驗收**

啟動前後端：

```bash
cd backend && uv run uvicorn BridgeUs_Django.asgi:application --host 0.0.0.0 --port 8000 --reload
```

```bash
cd frontend && npm run dev
```

確認：

1. 一般使用者登入 → 首頁每個議題**只有一張卡**（「開始對話」）。
2. 點進去 → 填問卷 → 全部答 4（中立）→ 直接進入 AI 對話，沒有出現模式選擇。
3. 另一個一般使用者 → 填出極端立場 → 進入配對等待畫面。
4. 在設定頁把配對逾時改成 1 分鐘 → 等待超過 1 分鐘後出現「要改成和 AI 代理人對話嗎？」→ 按「改成 AI 對話」→ 進入 AI 對話，**不需要重填問卷**。
5. 按「繼續等待」→ 提示消失，仍在等待畫面。
6. 一般使用者手動輸入 `/topic/102?mode=match` → 後端擋下（畫面顯示「請從議題頁面開始對話。」）。
7. 研究者登入 → 首頁每個議題仍是**兩張卡**，兩種模式都能正常進入。

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.jsx frontend/src/pages/HomePage.jsx frontend/src/components/IssueCard.jsx frontend/src/pages/TopicChat.jsx frontend/src/pages/TopicChat.css
git commit -m "feat(frontend): add mixed dialogue entry with ai fallback"
```

---

## Task 10: 顯示路徑優先讀分流紀錄

**Files:**
- Modify: `backend/api/views.py`（階段一建立的 `_display_stance_category`）
- Test: `backend/api/tests_mixed_entry.py`

階段一的 `_display_stance_category` 讀 `UserStanceProfile`。`DialogueEntryAssignment` 才是分流當下的權威紀錄（含當時生效的門檻），應優先。

- [ ] **Step 1: 寫失敗的測試**

在 `backend/api/tests_mixed_entry.py` 追加：

```python
class DisplayStanceCategoryPrefersAssignmentTests(TestCase):
    def test_assignment_wins_over_profile(self):
        from api.models import UserStanceProfile
        from api.views import _display_stance_category

        user = User.objects.create_user(
            username="prefers_assignment", password="pw-strong-12345"
        )
        UserStanceProfile.objects.create(
            user=user,
            topic_id=102,
            stance_score="6.00",
            stance_category="neutral",
            survey_answers={},
            survey_open_answers={},
        )
        DialogueEntryAssignment.objects.create(
            user=user,
            topic_id=102,
            route=DialogueEntryAssignment.Route.MATCH,
            stance_score="6.00",
            stance_category="support",
            support_threshold=4.5,
            oppose_threshold=3.5,
            entry_mode_at_assignment="mixed",
        )

        self.assertEqual(
            _display_stance_category(user_id=user.id, topic_id=102, stance_score=6.0),
            "support",
        )

    def test_falls_back_to_profile_without_assignment(self):
        from api.models import UserStanceProfile
        from api.views import _display_stance_category

        user = User.objects.create_user(
            username="no_assignment_profile", password="pw-strong-12345"
        )
        UserStanceProfile.objects.create(
            user=user,
            topic_id=102,
            stance_score="6.00",
            stance_category="support",
            survey_answers={},
            survey_open_answers={},
        )

        self.assertEqual(
            _display_stance_category(user_id=user.id, topic_id=102, stance_score=6.0),
            "support",
        )
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd backend && uv run pytest api/tests_mixed_entry.py::DisplayStanceCategoryPrefersAssignmentTests -v
```

Expected: `test_assignment_wins_over_profile` FAIL（得到 "neutral"）

- [ ] **Step 3: 實作**

在 `_display_stance_category` 內，把 `if user_id is not None:` 區塊改為：

```python
    if user_id is not None:
        # 分流紀錄優先：它是這場對話被分組當下的權威值，還記著當時生效的門檻。
        stored = (
            DialogueEntryAssignment.objects.filter(
                user_id=user_id, topic_id=topic_id
            )
            .values_list("stance_category", flat=True)
            .first()
        )
        if stored:
            return stored

        stored = (
            UserStanceProfile.objects.filter(user_id=user_id, topic_id=topic_id)
            .values_list("stance_category", flat=True)
            .first()
        )
        if stored:
            return stored
```

並把 docstring 裡的「階段二會在這裡優先讀 DialogueEntryAssignment…」那段註記刪除（已經做到了）。

- [ ] **Step 4: 執行測試確認通過**

```bash
cd backend && uv run pytest api/tests_mixed_entry.py::DisplayStanceCategoryPrefersAssignmentTests -v
```

Expected: PASS（2 個測試）

- [ ] **Step 5: Commit**

```bash
git add backend/api/views.py backend/api/tests_mixed_entry.py
git commit -m "feat(m3): prefer entry assignment for displayed stance category"
```

---

## 階段二驗收

- [ ] **執行完整測試**

```bash
cd backend && uv run pytest api/ apps/ -v
```

Expected: PASS（全部）

- [ ] **確認驗收條件**

1. 一般使用者首頁每議題只有一張卡；填完問卷後由後端分流，全程沒有模式選擇。
2. 中立立場進 AI 對話，極端立場進配對佇列。
3. 配對等待超過設定的分鐘數後出現詢問；接受後進入 AI 對話且不需重填問卷。
4. 一般使用者直接呼叫 `/api/matching/join/` 或 `/api/dialogue/sessions/` 被 403 擋下；改網址 `?mode=match` 沒有用。
5. 研究者維持分開的兩個入口，完全不受把關影響。
6. `DialogueEntryAssignment` 留下完整的分流紀錄：路由、當時 stance、當時門檻、是否 fallback。
