# Godot 配對綁定階段四：限時前測問卷 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 從 Godot 木樁配對跳轉進房的兩位玩家，各自填一份前測問卷，答案落地成真實的 s_pre（取代目前寫死的 `4.00` 佔位值），並讓 Godot 房的研究資料跟一般配對房同構。

**Architecture:** 分數欄位改 nullable，建房時留 NULL——「還沒有 s_pre」用 NULL 表達，不用 4.00 假裝有資料（`4.00` 跟真實分數無法區分）。建房時在 `stats.binding` 寫入來源與問卷期限。`/api/matching/status/` 的回應新增四個欄位讓前端知道「要不要跳問卷、剩多久、對方好了沒」。問卷送出走一支新端點，它建 `UserStanceProfile` + 一筆 MATCHED 的 `MatchQueueEntry`（後者同時是「這個人填過問卷」的憑證，見 spec §D5），並回填 `DialogueMatch` 的分數。

**Tech Stack:** Django + DRF + pytest、React + Vite。

**依據 spec:** `docs/superpowers/specs/2026-07-28-godot-identity-and-match-binding-design.md`
§D4（問卷排在跳轉之後）、§D5（queue entry 當填卷憑證）、§7.2（binding stats）、§8.1（status payload）、§8.2（端點契約）、§9.2（前端）、§9.3（倒數）、§10（資料完整性）

**前置：階段一～三已完成**（`c58a728..d944e2b`）。玩家已經能從 Godot 配對成功後自動跳轉到 `/topic/<id>?mode=match`，房間也建得出來——但房裡的 `user_a_score`/`user_b_score` 是 `4.00` 佔位值，而且 `TopicChat.jsx` 根本不會跳問卷（見 Task 4）。

---

## 範圍界線：本階段**不做**裁決

spec §D6 的三種收場（5 分鐘逾時、對方 45 秒沒心跳判定退出、退回一般模式）**全部是階段五**。本階段只做到「問卷能填、分數能回寫、倒數看得到」。

具體說，本階段結束時的**預期行為**（不是 bug，不要順手修）：

- `survey_deadline` 會回傳、倒數會顯示，但**歸零不會發生任何事**——沒有人會把房間作廢。
- `partner_state` 只有 `"pending"` / `"ready"` 兩種值，**沒有 `"left"`**。
- 一方始終不填問卷，另一方會永遠停在等待畫面。階段五才會處理。

會這樣切是因為裁決需要輪詢心跳當作存在訊號，而輪詢的閘門本身要在本階段先鬆開（Task 4）。先把資料流打通、再讓時間有意義。

---

## 執行前必讀

- 後端指令在 `backend/` 用 `uv run` 執行，**必須帶絕對路徑 cd**，否則 `uv run` 找不到虛擬環境並以 `Failed to spawn: pytest` 靜默失敗。
- **後端測試共用同一個 Postgres test database，一次只跑一個 pytest 程序。**
- ⚠️ **不要用 `pytest api`**——它會收集到 0 個測試然後以 exit code 5 結束，看起來像「跑完沒事」。專案測試檔命名是 `tests_*.py`，pytest 預設只收 `test_*.py`。一律指定檔名。
- **不要嘗試啟動 Godot**；本階段完全不動 `godot/`。
- 目前分支 `feat/Light`。**不要 amend、rebase、reset**——只新增新 commit。
- 註解與 docstring 用繁體中文。

### ⚠️ `force_authenticate` 會跳過 `authentication_classes`

DRF 的 `force_authenticate` 直接塞 `request.user`，**完全不走 authentication 流程**。本階段的新端點是一般使用者端點（`IsAuthenticated`），用 `force_authenticate` 測功能沒問題，但**至少要有一個測試用真 JWT**確認它真的擋未登入：

```python
from rest_framework_simplejwt.tokens import AccessToken

client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
```

## 檔案結構

| 檔案 | 動作 |
|---|---|
| `backend/api/models.py` | `DialogueMatch` 兩個分數欄位改 nullable、CHECK 允許 NULL |
| `backend/api/migrations/00XX_*.py` | 由 makemigrations 產生 |
| `backend/api/views.py` | 建房不再寫佔位分數、寫入 `stats.binding`；新增 `GodotSurveyView`；`_build_matching_state_payload` 加四欄 |
| `backend/api/serializers.py` | `MatchingStateSerializer` 加四欄；新增 `GodotSurveySerializer` |
| `backend/api/godot_binding.py`（新增） | `match_pretest_state()` / `godot_binding_info()`——「誰填過問卷」的唯一判斷入口 |
| `backend/apps/matching/services/matcher.py` | 新增 `record_godot_survey()` |
| `backend/api/urls.py` | 新增路由 |
| `backend/api/tests_godot_survey.py`（新增） | 本階段全部測試 |
| `frontend/src/pages/TopicChat.jsx` | 問卷觸發、送出目標、輪詢閘門、等待畫面 |
| `frontend/src/components/SurveyModal.jsx` | `deadline` prop 與倒數 |

---

## Task 1: 分數欄位改 nullable，廢除 4.00 佔位值

**Files:**
- Modify: `backend/api/models.py`
- Modify: `backend/api/views.py`（`GodotMatchRoomView`）
- Create: `backend/api/migrations/00XX_nullable_match_scores.py`（makemigrations 產生）
- Test: `backend/api/tests_godot_survey.py`（新建）

**為什麼**：`4.00` 是為了滿足 NOT NULL + CHECK 1..7 而編出來的值，跟真實分數**無法區分**（真的可能有人就是 4.00）。NULL 的語意才是「還沒有 s_pre」。唯一讀取端 `_post_dialogue_stance_snapshot`（`views.py:3073`）下游的 `fill_stance_metrics`（`models.py:555`）**已經有 `if s_pre is None` 守衛**，所以改 nullable 不會炸——但要有測試釘住。

- [ ] **Step 1: 寫失敗的測試**

建立 `backend/api/tests_godot_survey.py`：

```python
"""
pytest tests for Godot 綁定房的前測問卷（階段四）。

Run from backend/:
    pytest api/tests_godot_survey.py -v
"""
import pytest
from django.contrib.auth import get_user_model

from api.models import DialogueMatch

User = get_user_model()

SERVICE_TOKEN = "svc-token"


def _make_users():
    return (
        User.objects.create_user(username="ua", password="pw"),
        User.objects.create_user(username="ub", password="pw"),
    )


@pytest.mark.django_db
def test_godot_match_is_created_without_placeholder_scores():
    """建房時沒有真實 s_pre，分數留 NULL——4.00 佔位值跟真實分數無法區分。"""
    user_a, user_b = _make_users()

    match = DialogueMatch.objects.create(
        topic_id=102,
        user_a=user_a,
        user_b=user_b,
        matching_algorithm_version="godot_manual",
        room_id="room-null-scores",
        status=DialogueMatch.Status.ACTIVE,
    )

    match.refresh_from_db()
    assert match.user_a_score is None
    assert match.user_b_score is None


@pytest.mark.django_db
def test_score_check_constraint_still_rejects_out_of_range():
    """放寬成允許 NULL，但 1..7 的範圍檢查不能跟著失效。"""
    from django.db.utils import IntegrityError

    user_a, user_b = _make_users()

    with pytest.raises(IntegrityError):
        DialogueMatch.objects.create(
            topic_id=102,
            user_a=user_a,
            user_b=user_b,
            user_a_score=9,
            user_b_score=4,
            room_id="room-bad-score",
            status=DialogueMatch.Status.ACTIVE,
        )
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_survey.py -q`
Expected: 第一個測試以 `IntegrityError`（NOT NULL）失敗。

- [ ] **Step 3: 改 model**

`backend/api/models.py` 的 `DialogueMatch`：

```python
    # 建房當下不一定有 s_pre：Godot 木樁配對是先建房、跳轉之後才填前測問卷
    # （見 spec §D4）。NULL = 還沒填；不要用 4.00 之類的佔位值，那跟「真的
    # 填出 4.00」在資料上無法區分。
    user_a_score = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True
    )
    user_b_score = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True
    )
```

兩個 CHECK constraint 改成允許 NULL：

```python
            models.CheckConstraint(
                condition=Q(user_a_score__isnull=True)
                | (Q(user_a_score__gte=1) & Q(user_a_score__lte=7)),
                name="match_user_a_score_between_1_and_7",
            ),
            models.CheckConstraint(
                condition=Q(user_b_score__isnull=True)
                | (Q(user_b_score__gte=1) & Q(user_b_score__lte=7)),
                name="match_user_b_score_between_1_and_7",
            ),
```

- [ ] **Step 4: 建房不再寫佔位分數**

`backend/api/views.py` 的 `GodotMatchRoomView.post`，把 `DialogueMatch.objects.create(...)` 的 `user_a_score=Decimal("4.00")` 與 `user_b_score=Decimal("4.00")` 兩行**刪掉**，並改寫上方那段解釋佔位值的註解：

```python
        # Godot 木樁配對只做「同議題湊一對」，不跑 M3 立場向量配對。前測問卷是
        # 跳轉到網頁之後才填的（spec §D4），所以建房當下沒有 s_pre——兩個分數
        # 欄位留 NULL，等 /api/matching/godot-survey/ 回填。
        # matching_algorithm_version 標成 "godot_manual"，方便日後分析時跟真正
        # 演算法配對的資料分開看。
```

刪完用 `grep -n "Decimal" backend/api/views.py` 確認 `Decimal` 是否還有其他使用者；沒有就一併移除 import。

- [ ] **Step 5: 產生並套用 migration**

Run: `cd /Users/light/code/backend && uv run python manage.py makemigrations api`
Expected: 產生一個含 `AlterField` ×2 與 `RemoveConstraint`/`AddConstraint` ×2 的 migration。

Run: `cd /Users/light/code/backend && uv run python manage.py migrate api`
Expected: `Applying api.00XX_... OK`

- [ ] **Step 6: 跑測試確認通過**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_survey.py -q`
Expected: 2 passed。

- [ ] **Step 7: 確認後測問卷的 None 路徑沒壞**

`_post_dialogue_stance_snapshot` 現在可能回 None。讀 `backend/api/models.py` 的 `fill_stance_metrics`（約 555 行）確認它的 `if s_pre is None` 分支做了什麼，然後跑既有的後測問卷測試確認沒有迴歸：

Run: `cd /Users/light/code/backend && uv run pytest api/tests_post_questionnaire.py -q`
Expected: 全數 PASS。**若有失敗，停下來回報**——那代表 None 路徑其實沒被處理好，是真的問題，不要自己改測試繞過。

- [ ] **Step 8: Commit**

```bash
git add backend/api/models.py backend/api/views.py backend/api/migrations/ backend/api/tests_godot_survey.py
git commit -m "feat(m3): make match stance scores nullable and drop placeholder"
```

---

## Task 2: 建房寫入 binding stats + 填卷狀態判斷 + status payload 四個新欄位

**Files:**
- Modify: `backend/api/views.py`（`GodotMatchRoomView`、`_build_matching_state_payload`）
- Modify: `backend/api/serializers.py`（`MatchingStateSerializer`）
- Create: `backend/api/godot_binding.py`
- Test: `backend/api/tests_godot_survey.py`（追加）

**為什麼要有 `godot_binding.py`**：spec §10 明訂「誰填過問卷」的判斷只有一個合法入口。**不可以**用 `user_a_score is None` 之類的比對散落各處——那會在階段五的裁決、前端 payload、資料分析三個地方各長一份。

- [ ] **Step 1: 寫失敗的測試**

在 `backend/api/tests_godot_survey.py` 追加（檔頭補 `from django.utils import timezone`、`from datetime import timedelta`、`from rest_framework.test import APIClient`、`from api.models import MatchQueueEntry, UserStanceProfile`）：

```python
def _godot_match(user_a, user_b, *, topic_id=102, room_id="room-binding"):
    """建一間跟 GodotMatchRoomView 產出形狀相同的房（含 binding stats）。"""
    return DialogueMatch.objects.create(
        topic_id=topic_id,
        user_a=user_a,
        user_b=user_b,
        matching_algorithm_version="godot_manual",
        room_id=room_id,
        status=DialogueMatch.Status.ACTIVE,
        stats={
            "binding": {
                "source": "godot",
                "survey_deadline": (
                    timezone.now() + timedelta(seconds=300)
                ).isoformat(),
            }
        },
    )


@pytest.mark.django_db
def test_pretest_state_reports_nobody_done_initially():
    from api.godot_binding import match_pretest_state

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b)

    state = match_pretest_state(match)

    assert state["user_a_done"] is False
    assert state["user_b_done"] is False
    assert state["both_done"] is False


@pytest.mark.django_db
def test_pretest_state_counts_matched_queue_entry_as_done():
    """填過問卷的憑證是 MATCHED 的 queue entry，不是分數不等於某個值。"""
    from api.godot_binding import match_pretest_state

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b)
    profile = UserStanceProfile.objects.create(
        user=user_a, topic_id=102, stance_score=6, stance_category="support"
    )
    MatchQueueEntry.objects.create(
        user=user_a,
        topic_id=102,
        profile=profile,
        stance_score=6,
        status=MatchQueueEntry.Status.MATCHED,
        match=match,
    )

    state = match_pretest_state(match)

    assert state["user_a_done"] is True
    assert state["user_b_done"] is False
    assert state["both_done"] is False


@pytest.mark.django_db
def test_binding_info_returns_none_for_non_godot_match():
    from api.godot_binding import godot_binding_info

    user_a, user_b = _make_users()
    match = DialogueMatch.objects.create(
        topic_id=102,
        user_a=user_a,
        user_b=user_b,
        user_a_score=5,
        user_b_score=3,
        room_id="room-normal",
        status=DialogueMatch.Status.ACTIVE,
    )

    assert godot_binding_info(match) is None


@pytest.mark.django_db
def test_matching_status_exposes_binding_fields():
    user_a, user_b = _make_users()
    _godot_match(user_a, user_b)
    client = APIClient()
    client.force_authenticate(user=user_a)

    response = client.get("/api/matching/status/?topic_id=102")

    assert response.status_code == 200
    assert response.data["binding_source"] == "godot"
    assert response.data["survey_required"] is True
    assert response.data["survey_deadline"] is not None
    assert response.data["partner_state"] == "pending"


@pytest.mark.django_db
def test_matching_status_binding_fields_absent_for_normal_match():
    user_a, user_b = _make_users()
    DialogueMatch.objects.create(
        topic_id=102,
        user_a=user_a,
        user_b=user_b,
        user_a_score=5,
        user_b_score=3,
        room_id="room-normal-2",
        status=DialogueMatch.Status.ACTIVE,
    )
    client = APIClient()
    client.force_authenticate(user=user_a)

    response = client.get("/api/matching/status/?topic_id=102")

    assert response.data["binding_source"] is None
    assert response.data["survey_required"] is False
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_survey.py -q`
Expected: 新的 5 個測試失敗（`ModuleNotFoundError: api.godot_binding` 與缺欄位）。

- [ ] **Step 3: 建立 `backend/api/godot_binding.py`**

```python
"""Godot 綁定房的狀態判斷。

「這個人填過前測問卷沒有」在本專案只有一個合法判斷方式：他有沒有一筆對應這間房
的 MATCHED `MatchQueueEntry`（見 spec §D5、§10）。**不要**改用「分數是不是某個
特定值」之類的比對——真實分數可能剛好等於任何佔位值，而且那種判斷會散落在裁決、
API payload、資料分析三個地方各長一份。
"""
from api.models import MatchQueueEntry

BINDING_STATS_KEY = "binding"


def godot_binding_info(match) -> dict | None:
    """回傳這間房的 binding 資訊；不是 Godot 綁定房就回 None。"""
    if match is None:
        return None
    stats = match.stats if isinstance(match.stats, dict) else {}
    binding = stats.get(BINDING_STATS_KEY)
    if not isinstance(binding, dict) or binding.get("source") != "godot":
        return None
    return binding


def match_pretest_state(match) -> dict:
    """這間房兩位參與者各自填完前測問卷了沒。"""
    completed = set(
        MatchQueueEntry.objects.filter(
            match=match,
            status=MatchQueueEntry.Status.MATCHED,
        ).values_list("user_id", flat=True)
    )
    user_a_done = match.user_a_id in completed
    user_b_done = match.user_b_id in completed
    return {
        "user_a_done": user_a_done,
        "user_b_done": user_b_done,
        "both_done": user_a_done and user_b_done,
    }
```

- [ ] **Step 4: 建房寫入 binding stats**

`backend/api/views.py` 的 `GodotMatchRoomView.post`，`DialogueMatch.objects.create(...)` 加一個 `stats`：

```python
            stats={
                "binding": {
                    "source": "godot",
                    # 問卷期限。本階段只回傳給前端倒數用，逾時裁決在階段五。
                    "survey_deadline": (
                        timezone.now() + timedelta(seconds=GODOT_SURVEY_WINDOW_SECONDS)
                    ).isoformat(),
                }
            },
```

在 `views.py` 適當位置（靠近其他模組常數）加：

```python
# Godot 綁定房的前測問卷期限。本階段只用來顯示倒數；逾時作廢是階段五。
GODOT_SURVEY_WINDOW_SECONDS = 300
```

確認 `timezone` 與 `timedelta` 已 import（`grep -n "^from datetime import\|^from django.utils import timezone" backend/api/views.py`），沒有就補。

**冪等路徑（`if existing:`）不要重設 deadline**——沿用既有房間就沿用既有期限。

**注意**：`stats` 也被 presence 狀態用（`matcher.py` 的 `_save_presence_state`），但它是不同的 key 且會 `.copy()` 保留其他 key，兩者互不干擾。

- [ ] **Step 5: status payload 加四欄**

`backend/api/views.py` 的 `_build_matching_state_payload`，在 `payload = {...}` 裡加：

```python
        **_godot_binding_fields(match, user_id=user_id),
```

並在該函式上方新增：

```python
def _godot_binding_fields(match, *, user_id: int) -> dict:
    """Godot 綁定房專屬欄位。非綁定房一律回中性值，前端只在 binding_source
    為 "godot" 時使用其餘三欄。

    partner_state 本階段只有 pending/ready；"left"（對方退出）是階段五的裁決。
    """
    binding = godot_binding_info(match)
    if binding is None:
        return {
            "binding_source": None,
            "survey_required": False,
            "survey_deadline": None,
            "partner_state": None,
        }
    pretest = match_pretest_state(match)
    is_user_a = match.user_a_id == user_id
    self_done = pretest["user_a_done"] if is_user_a else pretest["user_b_done"]
    partner_done = pretest["user_b_done"] if is_user_a else pretest["user_a_done"]
    return {
        "binding_source": "godot",
        "survey_required": not self_done,
        "survey_deadline": binding.get("survey_deadline"),
        "partner_state": "ready" if partner_done else "pending",
    }
```

`views.py` 檔頭補 `from api.godot_binding import godot_binding_info, match_pretest_state`（跟其他 `api.*` / 相對 import 放一起，沿用該區塊既有風格）。

- [ ] **Step 6: serializer 加四欄**

`backend/api/serializers.py` 的 `MatchingStateSerializer` 末尾加：

```python
    binding_source = serializers.CharField(
        max_length=16, required=False, allow_null=True
    )
    survey_required = serializers.BooleanField(required=False)
    survey_deadline = serializers.DateTimeField(required=False, allow_null=True)
    partner_state = serializers.CharField(
        max_length=16, required=False, allow_null=True
    )
```

- [ ] **Step 7: 跑測試確認通過**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_survey.py -q`
Expected: 7 passed。

- [ ] **Step 8: Commit**

```bash
git add backend/api/godot_binding.py backend/api/views.py backend/api/serializers.py backend/api/tests_godot_survey.py
git commit -m "feat(m3): expose godot binding survey state in matching status"
```

---

## Task 3: `POST /api/matching/godot-survey/`

**Files:**
- Modify: `backend/apps/matching/services/matcher.py`（新增 `record_godot_survey`）
- Modify: `backend/api/views.py`（新增 `GodotSurveyView`）
- Modify: `backend/api/serializers.py`（新增 `GodotSurveySerializer`）
- Modify: `backend/api/urls.py`
- Test: `backend/api/tests_godot_survey.py`（追加）

- [ ] **Step 1: 寫失敗的測試**

追加：

```python
SURVEY_ANSWERS = {f"Q{i}": 6 for i in range(1, 9)}
SURVEY_OPEN = {"Q9": "我支持這個議題，因為……"}


@pytest.mark.django_db
def test_godot_survey_writes_real_score_and_queue_entry():
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-survey-1")
    client = APIClient()
    client.force_authenticate(user=user_a)

    response = client.post(
        "/api/matching/godot-survey/",
        {
            "topic_id": 102,
            "survey_answers": SURVEY_ANSWERS,
            "survey_open_answers": SURVEY_OPEN,
        },
        format="json",
    )

    assert response.status_code == 200
    match.refresh_from_db()
    assert match.user_a_score is not None
    assert match.user_a_score != 4  # 真實分數，不是舊的佔位值
    assert match.user_b_score is None  # 對方還沒填
    assert MatchQueueEntry.objects.filter(
        user=user_a, match=match, status=MatchQueueEntry.Status.MATCHED
    ).exists()
    assert UserStanceProfile.objects.filter(user=user_a, topic_id=102).exists()


@pytest.mark.django_db
def test_godot_survey_marks_route_match_even_for_neutral_stance():
    """spec §D3：Godot 房不套用「中立→AI」分流，一律 route=match。"""
    from api.models import DialogueEntryAssignment

    user_a, user_b = _make_users()
    _godot_match(user_a, user_b, room_id="room-survey-neutral")
    client = APIClient()
    client.force_authenticate(user=user_a)

    client.post(
        "/api/matching/godot-survey/",
        {
            "topic_id": 102,
            "survey_answers": {f"Q{i}": 4 for i in range(1, 9)},
            "survey_open_answers": SURVEY_OPEN,
        },
        format="json",
    )

    assignment = DialogueEntryAssignment.objects.get(user=user_a, topic_id=102)
    assert assignment.route == DialogueEntryAssignment.Route.MATCH


@pytest.mark.django_db
def test_godot_survey_both_sides_fills_likert_distance():
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-survey-2")
    client = APIClient()

    client.force_authenticate(user=user_a)
    client.post(
        "/api/matching/godot-survey/",
        {"topic_id": 102, "survey_answers": SURVEY_ANSWERS,
         "survey_open_answers": SURVEY_OPEN},
        format="json",
    )
    client.force_authenticate(user=user_b)
    response = client.post(
        "/api/matching/godot-survey/",
        {"topic_id": 102, "survey_answers": {f"Q{i}": 2 for i in range(1, 9)},
         "survey_open_answers": {"Q9": "我反對，因為……"}},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["survey_required"] is False
    assert response.data["partner_state"] == "ready"
    match.refresh_from_db()
    assert match.user_a_score is not None
    assert match.user_b_score is not None
    assert match.likert_distance > 0


@pytest.mark.django_db
def test_godot_survey_rejects_non_participant():
    user_a, user_b = _make_users()
    _godot_match(user_a, user_b, room_id="room-survey-3")
    outsider = User.objects.create_user(username="uc", password="pw")
    client = APIClient()
    client.force_authenticate(user=outsider)

    response = client.post(
        "/api/matching/godot-survey/",
        {"topic_id": 102, "survey_answers": SURVEY_ANSWERS,
         "survey_open_answers": SURVEY_OPEN},
        format="json",
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_godot_survey_rejects_when_no_godot_room():
    """一般配對房不能走這支端點——它的分數是配對演算法算出來的。"""
    user_a, user_b = _make_users()
    DialogueMatch.objects.create(
        topic_id=102, user_a=user_a, user_b=user_b,
        user_a_score=5, user_b_score=3,
        room_id="room-normal-3", status=DialogueMatch.Status.ACTIVE,
    )
    client = APIClient()
    client.force_authenticate(user=user_a)

    response = client.post(
        "/api/matching/godot-survey/",
        {"topic_id": 102, "survey_answers": SURVEY_ANSWERS,
         "survey_open_answers": SURVEY_OPEN},
        format="json",
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_godot_survey_resubmit_updates_instead_of_duplicating():
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-survey-4")
    client = APIClient()
    client.force_authenticate(user=user_a)
    payload = {"topic_id": 102, "survey_answers": SURVEY_ANSWERS,
               "survey_open_answers": SURVEY_OPEN}

    client.post("/api/matching/godot-survey/", payload, format="json")
    client.post("/api/matching/godot-survey/", payload, format="json")

    assert MatchQueueEntry.objects.filter(
        user=user_a, match=match, status=MatchQueueEntry.Status.MATCHED
    ).count() == 1


@pytest.mark.django_db
def test_godot_survey_requires_authentication():
    """用真 JWT 路徑驗證未登入會被擋——force_authenticate 驗不到這件事。"""
    response = APIClient().post(
        "/api/matching/godot-survey/",
        {"topic_id": 102, "survey_answers": SURVEY_ANSWERS,
         "survey_open_answers": SURVEY_OPEN},
        format="json",
    )

    assert response.status_code == 401
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_survey.py -q -k godot_survey`
Expected: 全數 404（路由不存在）。

- [ ] **Step 3: 服務層 `record_godot_survey`**

在 `backend/apps/matching/services/matcher.py` 的 `enqueue_for_matching` 之後加：

```python
def record_godot_survey(
    *,
    user,
    match,
    topic_id: int,
    stance_score: float,
    stance_category: str,
    survey_answers: dict,
    survey_open_answers: dict,
):
    """Godot 綁定房的前測問卷落地。

    跟 enqueue_for_matching 的差別：不排隊、不找候選人——配對已經由遊戲內的木樁
    決定了。這裡只補「s_pre」這一塊：建 profile、建一筆 MATCHED 的 queue entry
    （它同時是「這個人填過問卷」的憑證，見 spec §D5），並回填 DialogueMatch 的分數。

    建 queue entry 不只是為了憑證：下游（get_matching_state 的 profile 欄位、
    M5 分析、M6 pipeline）本來就預期配對房兩邊都有這筆記錄，順手建起來比另外加
    一個布林欄位更不容易跟既有邏輯打架。
    """
    decimal_score = _as_decimal(stance_score)
    q9_embedding = build_q9_embedding(survey_open_answers)

    with transaction.atomic():
        locked = DialogueMatch.objects.select_for_update().get(pk=match.pk)
        profile, _ = UserStanceProfile.objects.update_or_create(
            user=user,
            topic_id=topic_id,
            defaults={
                "stance_score": decimal_score,
                "stance_category": stance_category,
                "survey_answers": survey_answers,
                "survey_open_answers": survey_open_answers,
                "q9_embedding": q9_embedding,
            },
        )

        entry = MatchQueueEntry.objects.filter(
            user=user,
            topic_id=topic_id,
            match=locked,
            status=MatchQueueEntry.Status.MATCHED,
        ).first()
        if entry:
            # 重填問卷：覆寫同一筆，不要建第二筆（憑證必須是一對一）。
            entry.profile = profile
            entry.stance_score = decimal_score
            entry.save(update_fields=["profile", "stance_score", "updated_at"])
        else:
            MatchQueueEntry.objects.create(
                user=user,
                topic_id=topic_id,
                profile=profile,
                stance_score=decimal_score,
                status=MatchQueueEntry.Status.MATCHED,
                match=locked,
                matched_at=timezone.now(),
            )

        if locked.user_a_id == user.id:
            locked.user_a_score = decimal_score
        else:
            locked.user_b_score = decimal_score
        update_fields = ["user_a_score", "user_b_score"]
        if locked.user_a_score is not None and locked.user_b_score is not None:
            locked.likert_distance = _as_metric_decimal(
                abs(float(locked.user_a_score) - float(locked.user_b_score))
            )
            update_fields.append("likert_distance")
        locked.save(update_fields=update_fields)
        return locked
```

確認 `_as_metric_decimal`、`build_q9_embedding`、`UserStanceProfile`、`MatchQueueEntry`、`transaction`、`timezone` 在該檔案都已可用（`grep -n "_as_metric_decimal\|build_q9_embedding" backend/apps/matching/services/matcher.py`）；缺的照該檔既有 import 風格補上。

- [ ] **Step 4: serializer**

`backend/api/serializers.py` 加：

```python
class GodotSurveySerializer(serializers.Serializer):
    """Godot 綁定房的前測問卷。不含 restart_existing_match——這條路徑不排隊。"""

    topic_id = serializers.IntegerField()
    survey_answers = serializers.DictField(child=serializers.IntegerField())
    survey_open_answers = serializers.DictField(
        child=serializers.CharField(allow_blank=True), required=False, default=dict
    )
```

- [ ] **Step 5: view**

`backend/api/views.py`，放在 `MatchingStatusView` 附近：

```python
class GodotSurveyView(APIView):
    """POST /api/matching/godot-survey/ — Godot 綁定房的前測問卷回填。

    刻意**不**經過 _entry_gate_response：這條路徑的分組是遊戲內的木樁配對決定的，
    不是混合入口的立場分流決定的。中立立場的人也照樣 route=match（見 spec §D3）
    ——問卷是配對成立之後才填的，這時候再判定「你該去 AI」會把已經配好的兩人卡死。
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from apps.matching.services.matcher import record_godot_survey

        serializer = GodotSurveySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data
        topic_id = validated["topic_id"]

        match = (
            DialogueMatch.objects.filter(
                topic_id=topic_id,
                status=DialogueMatch.Status.ACTIVE,
            )
            .filter(Q(user_a=request.user) | Q(user_b=request.user))
            .order_by("-created_at")
            .first()
        )
        if match is None or godot_binding_info(match) is None:
            # 一般配對房也走這裡會被擋掉——它的分數是配對演算法算的，不該被覆寫。
            return Response(
                {"detail": "找不到屬於你的 Godot 配對房間。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        stance_score = _compute_user_stance_score(
            topic_id=topic_id, survey_answers=validated["survey_answers"]
        )
        stance_category = resolve_stance_category(
            topic_id=topic_id, user_stance_score=stance_score
        )
        resolved_open_answers = _resolve_open_answers(
            topic_id=topic_id,
            survey_open_answers=validated.get("survey_open_answers", {}),
        )

        record_godot_survey(
            user=request.user,
            match=match,
            topic_id=topic_id,
            stance_score=stance_score,
            stance_category=stance_category,
            survey_answers=validated["survey_answers"],
            survey_open_answers=resolved_open_answers,
        )

        support_threshold, oppose_threshold = get_stance_thresholds(topic_id=topic_id)
        DialogueEntryAssignment.objects.update_or_create(
            user=request.user,
            topic_id=topic_id,
            defaults={
                # 一律 match：見上方 docstring 與 spec §D3。
                "route": DialogueEntryAssignment.Route.MATCH,
                "stance_score": stance_score,
                "stance_category": stance_category,
                "support_threshold": support_threshold,
                "oppose_threshold": oppose_threshold,
                "entry_mode_at_assignment": get_entry_mode(
                    is_researcher=user_is_researcher(request.user)
                ),
                "fallback_offered_at": None,
                "fallback_accepted_at": None,
            },
        )

        from apps.matching.services.matcher import get_matching_state

        state = get_matching_state(user=request.user, topic_id=topic_id)
        return Response(
            _build_matching_state_payload(
                topic_id=topic_id, state=state, user_id=request.user.id
            )
        )
```

**實作前先確認這些名字在 `views.py` 都已存在且簽名相符**（它們都被 `DialogueEntryView` 用過）：`_compute_user_stance_score`、`resolve_stance_category`、`_resolve_open_answers`、`get_stance_thresholds`、`get_entry_mode`、`user_is_researcher`、`DialogueEntryAssignment`、`Q`。**簽名對不上就停下來回報，不要自己改造它們。**

- [ ] **Step 6: 路由**

`backend/api/urls.py`，在 `matching/cancel/` 那條附近加：

```python
    path('matching/godot-survey/', views.GodotSurveyView.as_view()),
```

- [ ] **Step 7: 跑測試確認通過**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_survey.py -q`
Expected: 14 passed。

- [ ] **Step 8: Commit**

```bash
git add backend/apps/matching/services/matcher.py backend/api/views.py backend/api/serializers.py backend/api/urls.py backend/api/tests_godot_survey.py
git commit -m "feat(m3): add godot binding pretest survey endpoint"
```

---

## Task 4: TopicChat 問卷觸發、送出目標、輪詢閘門

**Files:**
- Modify: `frontend/src/pages/TopicChat.jsx`

**這是本階段最容易寫錯的一支**，三個閘點都在不同地方，漏一個就會出現「問卷不跳」或「跳了但送錯端點」。

- [ ] **Step 1: 問卷觸發**

找到 `setShowSurvey(response.data.status === 'idle')`（約 858 行，在讀 `/api/matching/status/` 的那段）。改成：

```javascript
          // Godot 綁定房：房已經建好（status 是 matched），但前測問卷還沒填，
          // 所以不能只看 status === 'idle'——那個條件下 Godot 房永遠不會跳問卷。
          setShowSurvey(
            response.data.status === 'idle' || response.data.survey_required === true,
          );
```

同一個 effect 裡若還有其他 `setShowSurvey(true/false)` 的分支，**不要動**——只改這一處判斷式。

- [ ] **Step 2: 送出目標**

在 `handleSurveySubmit`（約 1596 行）的 `if (isMixedEntry) {` **之前**插入一個新分支：

```javascript
    // Godot 綁定房：配對已經由遊戲內的木樁決定了，這裡只是把 s_pre 補上。
    // 絕對不能走 /api/matching/join/——那會重新排隊，把已經綁好的房弄壞。
    if (matchingState?.binding_source === 'godot' && matchingState?.survey_required) {
      setMatchingError('');
      setIsMatchingActionLoading(true);
      try {
        const response = await api.post('/api/matching/godot-survey/', {
          topic_id: Number(id),
          survey_answers: answers,
          survey_open_answers: openAnswers,
        });
        if (!isChatPageMountedRef.current) return;
        setMatchingState(response.data);
        setShowSurvey(false);
      } catch (error) {
        if (!isChatPageMountedRef.current) return;
        setMatchingError(
          error?.response?.data?.detail || '目前無法送出問卷，請稍後再試。',
        );
      } finally {
        if (isChatPageMountedRef.current) {
          setIsMatchingActionLoading(false);
        }
      }
      return;
    }
```

- [ ] **Step 3: 鬆開輪詢閘門**

兩處都要改，缺一不可：

**(a)** 約 705 行的 `activeMatchRef` effect，目前 `showSurvey` 為真時把 `roomId`/`status` 設成 null。Godot 綁定情境要保留：

```javascript
  useEffect(() => {
    // Godot 綁定房在問卷期間仍要維持 room 追蹤：那段時間的輪詢同時是倒數的
    // 時間來源、以及（階段五）判斷對方還在不在的心跳。一般入口維持原本行為
    // ——問卷還沒送出前本來就還沒有房。
    const keepRoom = !showSurvey || matchingState?.binding_source === 'godot';
    activeMatchRef.current = {
      roomId: keepRoom ? matchingState?.room_id || null : null,
      status: keepRoom ? matchingState?.status || null : null,
      topicId: Number(id),
    };
```

（下面的 `leaveRequestSentRef` / `cancelQueueRequestSentRef` 兩段不動；dependency array 補上 `matchingState?.binding_source`。）

**(b)** 約 1035 行的輪詢 effect，目前只在 `status === 'matching'` 時跑：

```javascript
  useEffect(() => {
    const isGodotSurveyPending =
      matchingState?.binding_source === 'godot' && showSurvey;
    if (!isMatchingMode) {
      return undefined;
    }
    if (matchingState?.status !== 'matching' && !isGodotSurveyPending) {
      return undefined;
    }
```

（dependency array 補 `matchingState?.binding_source`、`showSurvey`。**輪詢間隔沿用既有值，不要改。**）

- [ ] **Step 4: 等待畫面**

自己填完但對方還沒填時，不要進聊天室。找到 `isMatchChatReady`（約 338 行）的定義，加一個條件：

```javascript
  const isGodotWaitingForPartner =
    matchingState?.binding_source === 'godot' &&
    matchingState?.survey_required === false &&
    matchingState?.partner_state === 'pending';
```

並在 `isMatchChatReady` 的判斷式裡加上 `&& !isGodotWaitingForPartner`。

在配對卡片的渲染（`renderMatchingCard`，約 2009 行）加一個分支，顯示「等待對方完成問卷」。**照該函式既有的 JSX 結構與 className 寫**，不要自創樣式。

- [ ] **Step 5: 自我檢查**

- `cd /Users/light/code/frontend && npm run lint 2>&1 | tail -5` 乾淨。
- `grep -n "binding_source" frontend/src/pages/TopicChat.jsx` 應該有 5 處（觸發、送出、兩個閘門、等待畫面）。
- 讀一次 `handleSurveySubmit`：確認 Godot 分支在 `isMixedEntry` 分支**之前**且有 `return`，不會兩條都跑。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/TopicChat.jsx
git commit -m "feat(m3): show and submit pretest survey for godot bound rooms"
```

---

## Task 5: SurveyModal 倒數

**Files:**
- Modify: `frontend/src/components/SurveyModal.jsx`
- Modify: `frontend/src/pages/TopicChat.jsx`（傳 prop）

- [ ] **Step 1: 加 `deadline` prop**

`SurveyModal` 的參數列加 `deadline = null`。元件內：

```javascript
  // 只有 Godot 強制綁定的問卷有期限；一般入口不傳這個 prop，不顯示倒數。
  // 歸零時前端不自己判定作廢——單一真值來源在後端（階段五的裁決），兩邊時鐘
  // 不一致的話會出現「一方以為還有時間、一方已經作廢」。
  const [remainingMs, setRemainingMs] = useState(null);

  useEffect(() => {
    if (!deadline) {
      setRemainingMs(null);
      return undefined;
    }
    const target = new Date(deadline).getTime();
    if (Number.isNaN(target)) {
      setRemainingMs(null);
      return undefined;
    }
    const tick = () => setRemainingMs(Math.max(0, target - Date.now()));
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [deadline]);
```

在 modal 標題區塊渲染（沿用檔案既有的 className 慣例）：

```jsx
      {remainingMs !== null && (
        <span className={remainingMs <= 60000 ? 'survey-countdown survey-countdown--urgent' : 'survey-countdown'}>
          剩餘 {String(Math.floor(remainingMs / 60000)).padStart(2, '0')}:
          {String(Math.floor((remainingMs % 60000) / 1000)).padStart(2, '0')}
        </span>
      )}
```

對應的 CSS 加在 `SurveyModal` 既有的樣式檔（若元件有獨立 css 檔就加那裡，沒有就沿用它現在的樣式來源）。`--urgent` 用紅色。

- [ ] **Step 2: TopicChat 傳 prop**

找到 `<SurveyModal ... />` 的使用處，加：

```jsx
        deadline={
          matchingState?.binding_source === 'godot'
            ? matchingState?.survey_deadline
            : null
        }
```

- [ ] **Step 3: 自我檢查**

`npm run lint` 乾淨；一般入口（沒有 `binding_source`）的問卷**不該**出現倒數。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/SurveyModal.jsx frontend/src/pages/TopicChat.jsx
git commit -m "feat(m3): show survey countdown for godot bound rooms"
```

---

## Task 6: 文件同步

**Files:**
- Modify: `docs/superpowers/specs/2026-07-28-godot-identity-and-match-binding-design.md`

- [ ] **Step 1: 更新**

- §3 現況表：「建房分數」「分數欄位限制」「前端問卷」三列改成已完成敘述。
- §8.1 / §8.2：確認欄位與端點契約跟實作一致（特別是 `partner_state` 本階段只有 pending/ready）。
- §10 資料完整性表：把「Godot 房，雙方填完 / 一邊真值一邊 4.00」改成 NULL 的敘述。
- §12 已知妥協第一條（4.00 佔位值）已解決，刪掉或改記「已於階段四改 nullable」。

**照程式碼寫，不要照本計畫的摘要寫。**

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-07-28-godot-identity-and-match-binding-design.md
git commit -m "docs: sync pretest survey flow into spec"
```

---

## 手動驗收清單（人類執行）

前置同階段三（Django 8005、vite 5173、Godot 靜態檔 8090、headless server 8085 帶 `GODOT_SERVICE_TOKEN`），兩個瀏覽器各登入不同帳號。

| # | 情境 | 預期 |
|---|---|---|
| 1 | 兩人坐同議題木樁 → 跳轉 | **兩邊都跳出前測問卷**，右上角有倒數（約 5:00 起跳） |
| 2 | A 先送出問卷 | A 看到「等待對方完成問卷」，**不會**進到聊天室 |
| 3 | B 也送出問卷 | 兩邊都進聊天室，能互傳訊息 |
| 4 | 檢查 DB | `user_a_score` / `user_b_score` 都是真實分數（不是 4.00、不是 NULL）；兩筆 MATCHED 的 `MatchQueueEntry`；`likert_distance` > 0 |
| 5 | 中立立場（全填 4）送出 | 照樣進得去；`DialogueEntryAssignment.route` 是 `match` 不是 `ai` |
| 6 | 一般議題入口（非 Godot）填問卷 | 行為完全不變，**沒有倒數**，仍走 `/api/dialogue/entry/` |
| 7 | 問卷放著 6 分鐘不填 | 倒數歸零後**不會有任何事發生**——這是階段五才做的裁決，不是 bug |
| 8 | 問卷期間開 devtools Network | 持續有 `/api/matching/status/` 的輪詢（階段五的心跳靠它） |

## 已知的殘餘（階段五）

- 5 分鐘逾時作廢、對方 45 秒無心跳判定退出、`partner_state = "left"`。
- 「退回一般模式」：已填問卷者依 stance 重新分流（極端→佇列、中立→AI）。
- `manage.py close_expired_godot_matches` 清理指令（兩人都關網頁時沒有人輪詢，裁決不會觸發）。
- 階段三審查留下的一筆：清理指令要涵蓋「建房成功但座位驗證失敗」留下的孤兒 ACTIVE 房，不是只處理問卷放棄。

## 動工前要確認的事

spec §15 第 1 點：**Godot 木樁配對不套用「中立 → AI」分流**（本階段 Task 3 的
`test_godot_survey_marks_route_match_even_for_neutral_stance` 就是在釘這個行為）。這會讓中立立場的
受試者出現在真人配對資料中，可用 `matching_algorithm_version="godot_manual"` 區分，但實驗設計上要有
說法——**建議先跟指導老師確認再開工**。
