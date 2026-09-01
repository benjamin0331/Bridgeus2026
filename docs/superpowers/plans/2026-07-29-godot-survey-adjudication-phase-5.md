# Godot 配對綁定階段五：問卷裁決與收尾 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓階段四的倒數真的有意義——5 分鐘內雙方沒填完問卷、或一方中途離開，房間要作廢，還留著的人退回一般配對模式；同時補齊 Godot 房的配對指標，並在後端擋住「前測未完成就進聊天室」。

**Architecture:** 裁決由輪詢驅動（階段四修好的輪詢正是心跳來源），寫在 `matcher.py` 的
`resolve_godot_survey_gate()`，由 `get_matching_state()` 在 Godot 綁定房上呼叫。兩人都關掉網頁時沒有人輪詢，由 `close_expired_godot_matches` 管理指令兜底。「退回一般模式」重用既有的 `can_enter_human_matching` 分流——階段四 §D3 刻意壓抑的規則，在房間作廢後恢復套用。

**Tech Stack:** Django + DRF + pytest、React + Vite。

**依據 spec:** `docs/superpowers/specs/2026-07-28-godot-identity-and-match-binding-design.md`
§D6（三種收場）、§7.3（裁決函式）、§7.4（退回一般模式）、§8.1（`partner_state`）、§9.2（前端反應）

**前置：階段一～四已完成**（`c58a728..7f1ae97`）。玩家已能從 Godot 配對成功、跳轉進房、看到限時問卷並送出，分數會回填成真實 s_pre。

---

## 本階段的三個來源

1. **spec §D6 的裁決**（主體）——階段四刻意留下的「倒數歸零不做事」。
2. **階段四總覽審查的兩個決議**（使用者確認要做）：
   - `semantic_distance` / `match_score` 對 Godot 房永遠是 `0`，跟階段四剛消滅的 `4.00` 是同一種「假值與真值無法區分」的問題。雙方填完後 `q9_embedding` 都在，補算得出來。
   - 聊天室訊息端點與 WebSocket **沒有後端把關**「雙方前測都完成」，目前只靠前端 `isMatchChatReady`。繞過 UI 就能在前測資料齊全前產生對話文字。
3. **階段三審查留下的一筆**：清理指令要涵蓋「建房成功但座位驗證失敗」留下的孤兒 ACTIVE 房，不是只處理問卷放棄。

---

## 執行前必讀

- 後端指令在 `backend/` 用 `uv run` 執行，**必須帶絕對路徑 cd**，否則 `uv run` 找不到虛擬環境並以 `Failed to spawn: pytest` 靜默失敗。
- **後端測試共用同一個 Postgres test database，一次只跑一個 pytest 程序。**
- ⚠️ **不要用 `pytest api`**——收集到 0 個測試、exit code 5，看起來像跑完沒事。一律指定檔名。
- **不要動 `godot/`**——本階段完全不碰 Godot 端。
- 目前分支 `feat/Light`。**不要 amend、rebase、reset**——只新增新 commit。
- 註解與 docstring 用繁體中文。

### ⚠️ 這個階段最容易寫錯的地方：誰是「離開」的那個人

階段四總覽審查特別警告過：**先送出問卷的那位，`last_seen` 的更新完全依賴輪詢**。階段四已經
把輪詞修成「一路輪詢到進聊天室為止」（commit `7181145`），所以他的 `last_seen` 會持續更新。
但如果你在實作時不小心把裁決寫成「誰的 `last_seen` 舊誰就是離開」而沒有考慮**根本沒連過線的
人**（`last_seen` 是 `None`），會把剛跳轉進來、還沒開始輪詢的人誤判成離開。

`_presence_state` 的預設值是 `{"connected": False, "last_seen": None, "disconnected_at": None}`
（`matcher.py:148`）。`last_seen` 為 `None` 代表「還沒出現過」，**不等於「離開了」**——房間剛建立
的頭幾秒兩個人都是這個狀態。裁決必須把 `None` 當成「還在寬限期內」，用房間的 `created_at` 起算，
而不是當成無限久以前。

## 檔案結構

| 檔案 | 動作 |
|---|---|
| `backend/apps/matching/services/matcher.py` | `resolve_godot_survey_gate()`、`_godot_return_to_normal()`；`record_godot_survey` 補算指標；`get_matching_state` 接上裁決 |
| `backend/api/godot_binding.py` | 新增 `survey_deadline_of()` / `binding_cancel_reason()` 讀取輔助 |
| `backend/api/views.py` | `partner_state` 支援 `"left"`；payload 加 `binding_cancel_reason`；房間端點加前測把關 |
| `backend/api/consumers.py` | WebSocket 連線加前測把關 |
| `backend/api/serializers.py` | `MatchingStateSerializer` 加 `binding_cancel_reason` |
| `backend/api/management/commands/close_expired_godot_matches.py`（新增） | 兜底清理 |
| `backend/api/tests_godot_adjudication.py`（新增） | 本階段全部測試 |
| `frontend/src/pages/TopicChat.jsx` | 反應 `partner_state === 'left'` 與作廢後的狀態 |

---

## Task 1: 補算 Godot 房的配對指標

**Files:**
- Modify: `backend/apps/matching/services/matcher.py`（`record_godot_survey`）
- Test: `backend/api/tests_godot_adjudication.py`（新建）

**為什麼**：`semantic_distance` 與 `match_score` 目前對 Godot 房永遠是欄位預設的 `0`——而真實
值也可能是 0，分析時無法區分「沒算」與「算出來是 0」。這正是階段四消滅 `4.00` 佔位值的同一個
問題。雙方送出問卷後兩份 `q9_embedding` 都在，用既有的 `calculate_match_score()` 算得出來。

**注意**：Godot 房的這兩個值**不是配對依據**（配對是遊戲內的木樁決定的），是**事後描述指標**。
註解要寫清楚，免得日後有人以為它參與了配對決策。

- [ ] **Step 1: 寫失敗的測試**

建立 `backend/api/tests_godot_adjudication.py`：

```python
"""
pytest tests for Godot 綁定房的問卷裁決與收尾（階段五）。

Run from backend/:
    pytest api/tests_godot_adjudication.py -v
"""
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from api.models import DialogueMatch, MatchQueueEntry, UserStanceProfile

User = get_user_model()

SUPPORT_ANSWERS = {str(i): (1 if i in {2, 4, 5, 6} else 7) for i in range(1, 9)}
OPPOSE_ANSWERS = {str(i): (7 if i in {2, 4, 5, 6} else 1) for i in range(1, 9)}
NEUTRAL_ANSWERS = {str(i): 4 for i in range(1, 9)}


def _make_users():
    return (
        User.objects.create_user(username="ua", password="pw"),
        User.objects.create_user(username="ub", password="pw"),
    )


def _godot_match(user_a, user_b, *, topic_id=102, room_id="room-adj",
                 deadline_offset_seconds=300):
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
                    timezone.now() + timedelta(seconds=deadline_offset_seconds)
                ).isoformat(),
            }
        },
    )


def _submit_survey(user, answers, *, topic_id=102, open_text="我的看法是……"):
    client = APIClient()
    client.force_authenticate(user=user)
    return client.post(
        "/api/matching/godot-survey/",
        {
            "topic_id": topic_id,
            "survey_answers": answers,
            "survey_open_answers": {"Q9": open_text},
        },
        format="json",
    )


@pytest.mark.django_db
def test_metrics_stay_zero_until_both_sides_submit():
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-metrics-1")

    _submit_survey(user_a, SUPPORT_ANSWERS)

    match.refresh_from_db()
    assert match.match_score == 0    # 只有一邊，還算不出來


@pytest.mark.django_db
def test_metrics_computed_once_both_sides_submit():
    """補算是事後描述指標，不是配對依據——但不能永遠留 0，那跟真值無法區分。"""
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-metrics-2")

    _submit_survey(user_a, SUPPORT_ANSWERS, open_text="我強烈支持，因為安全無虞。")
    _submit_survey(user_b, OPPOSE_ANSWERS, open_text="我強烈反對，因為風險太高。")

    match.refresh_from_db()
    assert match.likert_distance > 0
    assert match.match_score > 0
    # semantic_distance 取決於 embedding 模型，不斷言確切值，只確認有被寫入
    assert match.semantic_distance is not None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_adjudication.py -q`
Expected: `test_metrics_computed_once_both_sides_submit` 以 `match_score == 0` 失敗。

- [ ] **Step 3: 實作**

`matcher.py` 的 `record_godot_survey`，把目前只算 `likert_distance` 的那段換成完整補算：

```python
        update_fields = ["user_a_score", "user_b_score"]
        if locked.user_a_score is not None and locked.user_b_score is not None:
            # 兩邊都填完才算得出來。這兩個指標對 Godot 房是**事後描述**，不是配對
            # 依據——配對是遊戲內的木樁決定的，不是演算法挑的。但不能永遠留欄位
            # 預設的 0：真實的 semantic_distance 也可能是 0，留著就跟階段四消滅的
            # 4.00 佔位值一樣，分析時分不出「沒算」還是「算出來是 0」。
            profile_a = UserStanceProfile.objects.filter(
                user_id=locked.user_a_id, topic_id=topic_id
            ).first()
            profile_b = UserStanceProfile.objects.filter(
                user_id=locked.user_b_id, topic_id=topic_id
            ).first()
            metrics = calculate_match_score(
                requester_score=locked.user_a_score,
                candidate_score=locked.user_b_score,
                requester_embedding=profile_a.q9_embedding if profile_a else None,
                candidate_embedding=profile_b.q9_embedding if profile_b else None,
            )
            locked.likert_distance = _as_metric_decimal(metrics.likert_distance)
            locked.semantic_distance = _as_metric_decimal(metrics.semantic_distance)
            locked.match_score = _as_metric_decimal(metrics.match_score)
            update_fields += ["likert_distance", "semantic_distance", "match_score"]
        locked.save(update_fields=update_fields)
```

確認 `calculate_match_score` 已在該檔案 import（`grep -n "calculate_match_score" backend/apps/matching/services/matcher.py`）；沒有就照既有 import 風格從 `.matching_algorithm` 補上。

- [ ] **Step 4: 跑測試確認通過**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_adjudication.py -q`
Expected: 2 passed。

- [ ] **Step 5: 迴歸確認階段四沒被弄壞**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_survey.py -q`
Expected: 14 passed。

- [ ] **Step 6: Commit**

```bash
git add backend/apps/matching/services/matcher.py backend/api/tests_godot_adjudication.py
git commit -m "feat(m3): compute match metrics for godot rooms once both surveys land"
```

---

## Task 2: 裁決函式

**Files:**
- Modify: `backend/api/godot_binding.py`（新增讀取輔助）
- Modify: `backend/apps/matching/services/matcher.py`（`resolve_godot_survey_gate`、`_godot_return_to_normal`、接上 `get_matching_state`）
- Test: `backend/api/tests_godot_adjudication.py`（追加）

- [ ] **Step 1: 寫失敗的測試**

追加：

```python
GODOT_PRESENCE_TIMEOUT = 45


def _mark_seen(match, user, *, seconds_ago=0):
    """直接寫 presence，模擬「這個人最後一次輪詢是幾秒前」。"""
    from apps.matching.services.matcher import (
        MATCH_PRESENCE_STATS_KEY,
        _presence_state,
        _save_presence_state,
    )

    presence = _presence_state(match)
    presence["participants"][str(user.id)] = {
        "connected": True,
        "last_seen": (timezone.now() - timedelta(seconds=seconds_ago)).isoformat(),
        "disconnected_at": None,
    }
    _save_presence_state(match, presence)
    match.refresh_from_db()
    return match


@pytest.mark.django_db
def test_gate_keeps_room_while_both_recently_seen():
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-ok")
    _mark_seen(match, user_a, seconds_ago=2)
    _mark_seen(match, user_b, seconds_ago=3)

    resolved = resolve_godot_survey_gate(match=match)

    assert resolved.status == DialogueMatch.Status.ACTIVE


@pytest.mark.django_db
def test_never_seen_participant_is_not_treated_as_left():
    """房間剛建立時兩人的 last_seen 都是 None——那是「還沒出現」不是「離開」。

    誤判這個會讓剛跳轉進來、還沒開始輪詢的人被當成退出，房間當場作廢。
    """
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-fresh")

    resolved = resolve_godot_survey_gate(match=match)

    assert resolved.status == DialogueMatch.Status.ACTIVE


@pytest.mark.django_db
def test_partner_gone_cancels_room():
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-left")
    _mark_seen(match, user_a, seconds_ago=1)
    _mark_seen(match, user_b, seconds_ago=GODOT_PRESENCE_TIMEOUT + 10)

    resolved = resolve_godot_survey_gate(match=match)

    assert resolved.status == DialogueMatch.Status.CANCELLED
    assert resolved.stats["binding"]["cancel_reason"] == "godot_partner_left"


@pytest.mark.django_db
def test_deadline_expiry_cancels_room():
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(
        user_a, user_b, room_id="room-gate-timeout", deadline_offset_seconds=-10
    )
    _mark_seen(match, user_a, seconds_ago=1)
    _mark_seen(match, user_b, seconds_ago=1)

    resolved = resolve_godot_survey_gate(match=match)

    assert resolved.status == DialogueMatch.Status.CANCELLED
    assert resolved.stats["binding"]["cancel_reason"] == "godot_survey_timeout"


@pytest.mark.django_db
def test_deadline_expiry_does_not_cancel_when_both_completed():
    """逾時只針對「還沒填完」的房。都填完了就進聊天室，期限不再有意義。"""
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(
        user_a, user_b, room_id="room-gate-done", deadline_offset_seconds=-10
    )
    _submit_survey(user_a, SUPPORT_ANSWERS)
    _submit_survey(user_b, OPPOSE_ANSWERS)
    match.refresh_from_db()

    resolved = resolve_godot_survey_gate(match=match)

    assert resolved.status == DialogueMatch.Status.ACTIVE


@pytest.mark.django_db
def test_survivor_with_extreme_stance_returns_to_queue():
    """已填問卷、立場極端的人退回一般配對佇列（§D3 壓抑的分流在此恢復）。"""
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-survivor")
    _submit_survey(user_a, SUPPORT_ANSWERS)
    match.refresh_from_db()
    _mark_seen(match, user_a, seconds_ago=1)
    _mark_seen(match, user_b, seconds_ago=GODOT_PRESENCE_TIMEOUT + 10)

    resolve_godot_survey_gate(match=match)

    assert MatchQueueEntry.objects.filter(
        user=user_a, topic_id=102, status=MatchQueueEntry.Status.MATCHING
    ).exists()


@pytest.mark.django_db
def test_survivor_with_neutral_stance_routed_to_ai():
    from api.models import DialogueEntryAssignment
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-neutral")
    _submit_survey(user_a, NEUTRAL_ANSWERS)
    match.refresh_from_db()
    _mark_seen(match, user_a, seconds_ago=1)
    _mark_seen(match, user_b, seconds_ago=GODOT_PRESENCE_TIMEOUT + 10)

    resolve_godot_survey_gate(match=match)

    assignment = DialogueEntryAssignment.objects.get(user=user_a, topic_id=102)
    assert assignment.route == DialogueEntryAssignment.Route.AI


@pytest.mark.django_db
def test_gate_ignores_non_godot_match():
    from apps.matching.services.matcher import resolve_godot_survey_gate

    user_a, user_b = _make_users()
    match = DialogueMatch.objects.create(
        topic_id=102, user_a=user_a, user_b=user_b,
        user_a_score=5, user_b_score=3,
        room_id="room-normal-gate", status=DialogueMatch.Status.ACTIVE,
    )

    resolved = resolve_godot_survey_gate(match=match)

    assert resolved.status == DialogueMatch.Status.ACTIVE
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_adjudication.py -q`
Expected: 新的 8 個以 `ImportError`（`resolve_godot_survey_gate` 不存在）失敗。

- [ ] **Step 3: `godot_binding.py` 讀取輔助**

```python
def survey_deadline_of(match):
    """回傳這間 Godot 房的問卷期限（datetime）；不是綁定房或沒記期限就回 None。"""
    binding = godot_binding_info(match)
    if binding is None:
        return None
    from django.utils.dateparse import parse_datetime
    from django.utils import timezone as dj_timezone

    raw = binding.get("survey_deadline")
    if not raw:
        return None
    parsed = parse_datetime(raw) if isinstance(raw, str) else raw
    if parsed is None:
        return None
    if dj_timezone.is_naive(parsed):
        return dj_timezone.make_aware(parsed, dj_timezone.get_current_timezone())
    return parsed


def binding_cancel_reason(match) -> str | None:
    """房間若因裁決作廢，回傳原因代碼；否則 None。"""
    binding = godot_binding_info(match)
    return binding.get("cancel_reason") if binding else None
```

- [ ] **Step 4: 裁決與退回一般模式**

`matcher.py` 新增（放在 `record_godot_survey` 之後）：

```python
# Godot 綁定房的問卷階段，對方多久沒有輪詢就判定離開。
# 階段四已把前端輪詢改成「一路輪到進聊天室為止」，所以問卷期間與等待對方期間
# 都會持續更新 last_seen；這個門檻是輪詢間隔的數倍，容忍網路抖動與換頁。
DEFAULT_GODOT_PRESENCE_TIMEOUT_SECONDS = 45


def godot_presence_timeout_seconds() -> int:
    try:
        return max(
            0,
            int(
                os.getenv(
                    "GODOT_PRESENCE_TIMEOUT_SECONDS",
                    str(DEFAULT_GODOT_PRESENCE_TIMEOUT_SECONDS),
                )
            ),
        )
    except (TypeError, ValueError):
        return DEFAULT_GODOT_PRESENCE_TIMEOUT_SECONDS


def _godot_participant_is_gone(match, user_id: int, *, now, timeout_seconds: int) -> bool:
    """這位參與者是不是已經離開（超過門檻沒有輪詢）。

    last_seen 為 None 代表「還沒出現過」——房間剛建立的頭幾秒兩個人都是這樣，
    **不能當成離開**，否則剛跳轉進來還沒開始輪詢的人會被當場判出局。這種情況
    改用房間的 created_at 起算寬限期。
    """
    presence = _presence_state(match)
    participant = presence["participants"].get(str(user_id), {})
    last_seen = _parse_presence_datetime(participant.get("last_seen"))
    reference = last_seen or match.created_at
    return (now - reference).total_seconds() > timeout_seconds


def resolve_godot_survey_gate(*, match, now=None):
    """Godot 綁定房在雙方完成前測問卷前的裁決。

    由 get_matching_state() 在輪詢路徑上呼叫——雙方在問卷階段與等待階段都會
    持續輪詢，那就是這裡用的存在訊號。兩人都關掉網頁時沒有人輪詢，裁決不會
    觸發，由 close_expired_godot_matches 指令兜底。

    先判離開再判逾時：離開的訊息（「對方已退出」）比「時間到了」對使用者具體。
    """
    from api.godot_binding import (
        BINDING_STATS_KEY,
        godot_binding_info,
        match_pretest_state,
        survey_deadline_of,
    )

    if godot_binding_info(match) is None:
        return match
    if match.status != DialogueMatch.Status.ACTIVE:
        return match

    pretest = match_pretest_state(match)
    if pretest["both_done"]:
        # 都填完了就進聊天室，期限與存在偵測不再適用——之後改由既有的
        # close_match_if_idle / close_match_if_participant_absent 接手。
        return match

    current_time = now or timezone.now()
    timeout_seconds = godot_presence_timeout_seconds()
    reason = None

    if timeout_seconds > 0:
        for user_id in (match.user_a_id, match.user_b_id):
            if _godot_participant_is_gone(
                match, user_id, now=current_time, timeout_seconds=timeout_seconds
            ):
                reason = "godot_partner_left"
                break

    if reason is None:
        deadline = survey_deadline_of(match)
        if deadline is not None and current_time > deadline:
            reason = "godot_survey_timeout"

    if reason is None:
        return match

    with transaction.atomic():
        locked = DialogueMatch.objects.select_for_update().get(pk=match.pk)
        if locked.status != DialogueMatch.Status.ACTIVE:
            return locked
        stats = _stats_dict(locked).copy()
        binding = dict(stats.get(BINDING_STATS_KEY) or {})
        binding["cancel_reason"] = reason
        binding["cancelled_at"] = _isoformat(current_time)
        stats[BINDING_STATS_KEY] = binding
        locked.stats = stats
        locked.status = DialogueMatch.Status.CANCELLED
        locked.closed_at = current_time
        locked.save(update_fields=["stats", "status", "closed_at"])

    # 房間作廢後才退回一般模式：已填問卷的人有 stance 資料可以重新分流。
    # 這裡是階段四 §D3 刻意壓抑的分流規則恢復生效的地方。
    for user_id, done in (
        (locked.user_a_id, pretest["user_a_done"]),
        (locked.user_b_id, pretest["user_b_done"]),
    ):
        if done:
            _godot_return_to_normal(user_id=user_id, topic_id=locked.topic_id)
    return locked


def _godot_return_to_normal(*, user_id: int, topic_id: int) -> None:
    """把已填過問卷的參與者退回一般模式。

    §D3 在 Godot 房裡刻意不套用「中立→AI」分流（問卷是配對成立後才填的，這時
    判定某人該去 AI 會把已配好的兩人卡死）。但房間作廢之後那個顧慮消失了——
    這個人現在是單獨一個人，本來就該照他的立場走正常分流。
    """
    from api.models import DialogueEntryAssignment, UserStanceProfile

    profile = (
        UserStanceProfile.objects.filter(user_id=user_id, topic_id=topic_id)
        .order_by("-updated_at", "-id")
        .first()
    )
    if profile is None:
        return

    # 舊的 MATCHED queue entry 屬於已作廢的房，標成 CANCELLED，否則
    # enqueue_for_matching 會撞上 uniq_active_queue_user_topic 或被誤讀成仍在配對。
    MatchQueueEntry.objects.filter(
        user_id=user_id,
        topic_id=topic_id,
        status=MatchQueueEntry.Status.MATCHED,
    ).update(status=MatchQueueEntry.Status.CANCELLED, cancelled_at=timezone.now())

    if _can_enter_human_matching(profile.stance_category):
        enqueue_for_matching(
            user=profile.user,
            topic_id=topic_id,
            stance_score=float(profile.stance_score),
            stance_category=profile.stance_category,
            survey_answers=profile.survey_answers,
            survey_open_answers=profile.survey_open_answers,
        )
        route = DialogueEntryAssignment.Route.MATCH
    else:
        route = DialogueEntryAssignment.Route.AI

    DialogueEntryAssignment.objects.filter(
        user_id=user_id, topic_id=topic_id
    ).update(route=route)
```

確認 `os`、`_stats_dict`、`_isoformat`、`enqueue_for_matching`、`_can_enter_human_matching` 在該檔案都可用。

- [ ] **Step 5: 接上 `get_matching_state`**

在 `get_matching_state` 裡，`active_match` 取得之後、既有的 `close_match_if_participant_absent`
**之前**插入：

```python
    if active_match:
        # Godot 綁定房在雙方填完問卷前，適用的是問卷裁決而不是一般的缺席/閒置關房
        # （那兩者的預設值分別是 180s／600s，跟問卷階段的語意不同）。
        active_match = resolve_godot_survey_gate(match=active_match)
```

**注意順序**：裁決要在既有的 `close_match_if_participant_absent` / `close_match_if_idle` 之前，
因為那兩者只處理 `ACTIVE` 房，裁決把房改成 `CANCELLED` 之後它們自然不會再動它。

- [ ] **Step 6: 跑測試確認通過**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_adjudication.py -q`
Expected: 10 passed。

- [ ] **Step 7: 迴歸**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_survey.py -q`
Expected: 14 passed。

Run: `cd /Users/light/code/backend && uv run pytest api/tests_mixed_entry.py -q`
Expected: 47 passed。**這個很重要**——裁決插進了 `get_matching_state`，那是混合入口也會走的路徑。

- [ ] **Step 8: Commit**

```bash
git add backend/api/godot_binding.py backend/apps/matching/services/matcher.py backend/api/tests_godot_adjudication.py
git commit -m "feat(m3): adjudicate godot survey deadline and partner departure"
```

---

## Task 3: `partner_state = "left"` 與 `binding_cancel_reason`

**Files:**
- Modify: `backend/api/views.py`（`_godot_binding_fields`）
- Modify: `backend/api/serializers.py`
- Test: `backend/api/tests_godot_adjudication.py`（追加）

- [ ] **Step 1: 寫失敗的測試**

```python
@pytest.mark.django_db
def test_status_reports_partner_left_after_cancellation():
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-status-left")
    _submit_survey(user_a, SUPPORT_ANSWERS)
    match.refresh_from_db()
    _mark_seen(match, user_a, seconds_ago=1)
    _mark_seen(match, user_b, seconds_ago=GODOT_PRESENCE_TIMEOUT + 10)

    client = APIClient()
    client.force_authenticate(user=user_a)
    response = client.get("/api/matching/status/?topic_id=102")

    assert response.status_code == 200
    assert response.data["binding_cancel_reason"] == "godot_partner_left"
```

- [ ] **Step 2: 跑測試確認失敗**

- [ ] **Step 3: 實作**

`_godot_binding_fields` 改成也讀取作廢原因，並在房間已作廢時把 `partner_state` 標成 `"left"`：

```python
def _godot_binding_fields(match, *, user_id: int) -> dict:
    """Godot 綁定房專屬欄位。非綁定房一律回中性值，前端只在 binding_source
    為 "godot" 時使用其餘欄位。
    """
    binding = godot_binding_info(match)
    if binding is None:
        return {
            "binding_source": None,
            "survey_required": False,
            "survey_deadline": None,
            "partner_state": None,
            "binding_cancel_reason": None,
        }
    cancel_reason = binding.get("cancel_reason")
    pretest = match_pretest_state(match)
    is_user_a = match.user_a_id == user_id
    self_done = pretest["user_a_done"] if is_user_a else pretest["user_b_done"]
    partner_done = pretest["user_b_done"] if is_user_a else pretest["user_a_done"]
    if cancel_reason:
        partner_state = "left"
    elif partner_done:
        partner_state = "ready"
    else:
        partner_state = "pending"
    return {
        "binding_source": "godot",
        "survey_required": not self_done and not cancel_reason,
        "survey_deadline": binding.get("survey_deadline"),
        "partner_state": partner_state,
        "binding_cancel_reason": cancel_reason,
    }
```

serializer 加：

```python
    binding_cancel_reason = serializers.CharField(
        max_length=32, required=False, allow_null=True
    )
```

**注意**：房間作廢後 `get_matching_state` 回的可能已經不是這間房（使用者被退回一般模式後
可能已經有新的排隊記錄）。這個欄位只在裁決發生的那一次輪詢會被讀到，前端要在收到當下就
反應，不能指望它一直存在。這一點寫進註解。

- [ ] **Step 4: 送出端點先裁決再接受（階段五 Task 2 實作時發現的缺口）**

spec §8.2 原本就要求 `GodotSurveyView` 在接受問卷前先跑一次裁決、房已作廢就回 409。階段四
寫這支端點時裁決還不存在，所以沒做——結果是對一間已作廢的房送出問卷，答案會先被寫進資料庫，
使用者才在回應裡看到房間是 CANCELLED。

在 `GodotSurveyView.post` 找到 match、確認是 Godot 綁定房之後、`_compute_user_stance_score`
**之前**插入：

```python
        from apps.matching.services.matcher import resolve_godot_survey_gate

        # 先裁決再接受：房間若已因逾時或對方退出而作廢，不該再收問卷答案
        # （寫進去也沒有意義，而且會讓使用者以為送出成功）。見 spec §8.2。
        match = resolve_godot_survey_gate(match=match)
        if match.status != DialogueMatch.Status.ACTIVE:
            return Response(
                {
                    "detail": "這個配對房間已結束。",
                    "binding_cancel_reason": binding_cancel_reason(match),
                },
                status=status.HTTP_409_CONFLICT,
            )
```

`binding_cancel_reason` 從 `api.godot_binding` import。

補一個測試：

```python
@pytest.mark.django_db
def test_survey_submission_rejected_after_room_cancelled():
    """房已作廢就不該再收問卷——寫進去沒有意義，還會讓使用者以為送出成功。"""
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-submit-after-cancel")
    _mark_seen(match, user_a, seconds_ago=1)
    _mark_seen(match, user_b, seconds_ago=GODOT_PRESENCE_TIMEOUT + 10)

    response = _submit_survey(user_a, SUPPORT_ANSWERS)

    assert response.status_code == 409
    assert response.data["binding_cancel_reason"] == "godot_partner_left"
    assert not UserStanceProfile.objects.filter(user=user_a, topic_id=102).exists()
```

- [ ] **Step 5: 測試通過並 commit**

```bash
git commit -m "feat(m3): report partner departure and cancel reason in matching status"
```

---

## Task 4: 後端把關——前測未完成不得進聊天室

**Files:**
- Modify: `backend/api/views.py`（`MatchingRoomMessagesView` 的 GET 與 POST）
- Modify: `backend/api/consumers.py`（`connect`）
- Test: `backend/api/tests_godot_adjudication.py`（追加）

**為什麼**：目前只有前端 `isMatchChatReady` 擋著。繞過 UI（直接打 API 或開 WS）就能在前測資料
齊全前產生對話文字——研究資料上會出現「對話早於 s_pre」的紀錄。

- [ ] **Step 1: 寫失敗的測試**

```python
@pytest.mark.django_db
def test_room_messages_blocked_until_both_pretests_done():
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-msg")
    _submit_survey(user_a, SUPPORT_ANSWERS)

    client = APIClient()
    client.force_authenticate(user=user_a)
    response = client.get(f"/api/matching/rooms/{match.room_id}/messages/")

    assert response.status_code == 409


@pytest.mark.django_db
def test_room_messages_allowed_once_both_pretests_done():
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-gate-msg-ok")
    _submit_survey(user_a, SUPPORT_ANSWERS)
    _submit_survey(user_b, OPPOSE_ANSWERS)

    client = APIClient()
    client.force_authenticate(user=user_a)
    response = client.get(f"/api/matching/rooms/{match.room_id}/messages/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_normal_match_room_is_not_gated():
    """一般配對房建房時就有分數，沒有「前測未完成」這種狀態，不該被擋。"""
    user_a, user_b = _make_users()
    match = DialogueMatch.objects.create(
        topic_id=102, user_a=user_a, user_b=user_b,
        user_a_score=5, user_b_score=3,
        room_id="room-normal-msg", status=DialogueMatch.Status.ACTIVE,
    )

    client = APIClient()
    client.force_authenticate(user=user_a)
    response = client.get(f"/api/matching/rooms/{match.room_id}/messages/")

    assert response.status_code == 200
```

- [ ] **Step 2: 跑測試確認失敗**

- [ ] **Step 3: 實作共用把關**

`views.py` 新增：

```python
def _godot_pretest_incomplete_response(match):
    """Godot 綁定房在雙方前測完成前不得進聊天室；未完成回 Response，完成或
    非綁定房回 None。

    前端已有 isMatchChatReady 擋著，但那只是 UI——繞過它就能在前測資料齊全前
    產生對話文字，研究資料上會出現「對話早於 s_pre」的紀錄。把關要在後端。
    """
    if godot_binding_info(match) is None:
        return None
    if match_pretest_state(match)["both_done"]:
        return None
    return Response(
        {"detail": "雙方都完成前測問卷後才能開始對話。"},
        status=status.HTTP_409_CONFLICT,
    )
```

在 `MatchingRoomMessagesView` 的 **GET 與 POST 兩者**，`_get_room_match_for_user` 取得 match、
確認非 None 之後插入：

```python
        gate = _godot_pretest_incomplete_response(match)
        if gate is not None:
            return gate
```

- [ ] **Step 4: WebSocket 把關**

`consumers.py` 的配對房 consumer `connect()`，在 `self.match` 取得成功之後、
`_refresh_current_match_for_activity()` 之前插入：

```python
        if not await self._godot_pretest_complete():
            # 雙方前測未完成前不建立連線，理由同 REST 端點（見
            # views.py::_godot_pretest_incomplete_response）。
            await self.close(code=4009)
            return
```

並新增：

```python
    @database_sync_to_async
    def _godot_pretest_complete(self) -> bool:
        from api.godot_binding import godot_binding_info, match_pretest_state

        if godot_binding_info(self.match) is None:
            return True
        return match_pretest_state(self.match)["both_done"]
```

**照該檔案既有的 `database_sync_to_async` 用法寫**——先讀一遍
`_get_active_match_for_user` 附近的寫法確認裝飾器與 import 風格。

- [ ] **Step 5-6: 測試通過並 commit**

Run 迴歸：`cd /Users/light/code/backend && uv run pytest api/tests_websocket.py -q`
⚠️ **這個檔案在 base commit 就是壞的**（階段二查證過：10 failed + 11 errors，Postgres 連線問題，
與本專案改動無關）。跑它只是確認**沒有變得更糟**，不要試圖修好它。

```bash
git commit -m "feat(m3): gate match room on both pretests being complete"
```

---

## Task 5: 清理指令

**Files:**
- Create: `backend/api/management/commands/close_expired_godot_matches.py`
- Test: `backend/api/tests_godot_adjudication.py`（追加）

**為什麼**：裁決是輪詢驅動的，兩人都關掉網頁時沒有人觸發。另外階段三審查留了一筆：
建房成功但座位驗證失敗（有人中途取消）也會留下孤兒 ACTIVE 房，那種房從來沒有人進來過。

- [ ] **Step 1: 寫失敗的測試**

```python
@pytest.mark.django_db
def test_command_cancels_expired_unattended_room():
    from django.core.management import call_command

    user_a, user_b = _make_users()
    match = _godot_match(
        user_a, user_b, room_id="room-cmd-1", deadline_offset_seconds=-60
    )

    call_command("close_expired_godot_matches")

    match.refresh_from_db()
    assert match.status == DialogueMatch.Status.CANCELLED


@pytest.mark.django_db
def test_command_dry_run_writes_nothing():
    from django.core.management import call_command

    user_a, user_b = _make_users()
    match = _godot_match(
        user_a, user_b, room_id="room-cmd-2", deadline_offset_seconds=-60
    )

    call_command("close_expired_godot_matches", "--dry-run")

    match.refresh_from_db()
    assert match.status == DialogueMatch.Status.ACTIVE


@pytest.mark.django_db
def test_command_leaves_rooms_within_deadline():
    from django.core.management import call_command

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-cmd-3")

    call_command("close_expired_godot_matches")

    match.refresh_from_db()
    assert match.status == DialogueMatch.Status.ACTIVE


@pytest.mark.django_db
def test_command_leaves_completed_rooms_alone():
    """雙方都填完的房已經在對話了，逾期與否都不該被指令收掉。"""
    from django.core.management import call_command

    user_a, user_b = _make_users()
    match = _godot_match(
        user_a, user_b, room_id="room-cmd-4", deadline_offset_seconds=-60
    )
    _submit_survey(user_a, SUPPORT_ANSWERS)
    _submit_survey(user_b, OPPOSE_ANSWERS)

    call_command("close_expired_godot_matches")

    match.refresh_from_db()
    assert match.status == DialogueMatch.Status.ACTIVE
```

- [ ] **Step 2: 跑測試確認失敗**

- [ ] **Step 3: 實作**

沿用 `close_stale_dialogue_sessions.py` 的形式（`--dry-run`、先列出再寫入、no-op 時明說）：

```python
"""收掉逾期而且沒人在照顧的 Godot 綁定配對房。

為什麼需要這支：問卷階段的裁決（resolve_godot_survey_gate）是輪詢驅動的，
兩位參與者都關掉網頁時沒有人觸發，房間會一直掛在 ACTIVE。另外還有一種孤兒房
——Godot 端建房成功但座位驗證失敗（有人中途取消配對），那種房從來沒有人進來過。

用法（在 backend/ 底下）：
    python manage.py close_expired_godot_matches --dry-run
    python manage.py close_expired_godot_matches

建議每分鐘跑一次。指令是冪等的，沒有東西要收時是 no-op。
"""
```

實作要點（照著寫，細節自己補）：
- 只掃 `status=ACTIVE` 且 `godot_binding_info(match) is not None` 的房。
- **跳過 `match_pretest_state(match)["both_done"]` 的房**——那些已經在對話，該由既有的
  `close_match_if_idle` 處理，不是這支的責任。
- 逾期判斷用 `survey_deadline_of(match)`；沒有記期限的房（理論上不該有）用 `created_at`
  加上 `GODOT_SURVEY_WINDOW_SECONDS` 當備援，並在輸出裡標示出來。
- 收掉的方式**直接重用 `resolve_godot_survey_gate(match=match)`**，不要自己寫一份作廢邏輯
  ——退回一般模式的處理也在裡面，複製一份必然會漏。

- [ ] **Step 4-5: 測試通過並 commit**

```bash
git commit -m "feat(m3): add close_expired_godot_matches cleanup command"
```

---

## Task 6: 前端反應作廢

**Files:**
- Modify: `frontend/src/pages/TopicChat.jsx`

- [ ] **Step 1: 收到作廢時的處理**

在輪詢的回應處理裡，收到 `binding_cancel_reason` 時：關閉問卷、顯示訊息、讓後端回傳的新狀態接手。

```javascript
        // 房間被裁決作廢（對方退出或問卷逾時）。後端已經把還留著的人退回一般
        // 模式，所以這裡收下新狀態就好，不要自己決定下一步該去哪。
        // 這個欄位只在裁決發生的那一次輪詢會出現，要在收到當下就反應。
        if (response.data.binding_cancel_reason) {
          setShowSurvey(false);
          setMatchingError(
            response.data.binding_cancel_reason === 'godot_partner_left'
              ? '對方已退出配對，已為你轉回一般配對模式。'
              : '前測問卷逾時，已為你轉回一般配對模式。',
          );
        }
```

**照該檔案既有的輪詢回應處理結構寫**——先讀一遍 `setMatchingState(response.data)` 前後的邏輯
確認插入位置正確。

- [ ] **Step 2: 自我檢查**

- `cd /Users/light/code/frontend && npm run lint 2>&1 | tail -5` 乾淨。
- 確認作廢後 `isGodotWaitingForPartner` 不會再是 true（`survey_required` 已被後端設為 false、
  `partner_state` 是 `'left'`），使用者不會卡在等待畫面。

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(m3): surface godot room cancellation in topic chat"
```

---

## 手動驗收清單（人類執行）

前置同階段三、四（四個 process；見 `godot/CLAUDE.md` 的 Running 與本專案的本機測試拓樸）。

| # | 情境 | 預期 |
|---|---|---|
| 1 | 兩人跳轉進房，雙方在 5 分鐘內填完 | 正常進聊天室；DB 中 `match_score` / `semantic_distance` **不是 0** |
| 2 | A 填完，B 關掉分頁 | A 在 ~45 秒內看到「對方已退出配對，已為你轉回一般配對模式」；DB 房間 `CANCELLED`、`cancel_reason=godot_partner_left` |
| 3 | 承上，A 立場極端 | A 出現在一般配對佇列（`MatchQueueEntry` status=`matching`） |
| 4 | 承上但 A 立場中立 | A 的 `DialogueEntryAssignment.route` 變成 `ai` |
| 5 | 兩人都不填，放著超過 5 分鐘 | 房間 `CANCELLED`、`cancel_reason=godot_survey_timeout`（其中一人還開著頁面時由輪詢觸發） |
| 6 | 兩人都關掉分頁，放著超過 5 分鐘，然後跑清理指令 | 房間被收掉 |
| 7 | **只有一方填完問卷時，用 curl 直接打房間訊息 API** | 回 409，不是 200 |
| 8 | 承上，用 WS 直接連房間 | 連線被關（code 4009） |
| 9 | 一般議題入口的配對（非 Godot） | 完全不受影響——沒有倒數、不會被裁決作廢、房間訊息不被擋 |

情境 2 的計時：`GODOT_PRESENCE_TIMEOUT_SECONDS` 環境變數可調，測試時可以設成 `10` 縮短等待，
**測完記得改回來**。

## 已知的殘餘（不在本階段）

- 歷史 `godot_manual` 房仍帶著階段四之前寫入的 `4.00`（真值與佔位值無法區分）。要清的話需要
  一次性 backfill，且要決定「哪些算是佔位」——建議條件是 `matching_algorithm_version=
  "godot_manual"` 且沒有對應的 MATCHED queue entry。
- `receive_chat` / `receive_voice` / `receive_invite` 的發話者驗證、語音頻寬（每幀原始 PCM
  ≈ 2.8 Mbps）——Godot 端 code review 待辦，另案。
- `api/tests_websocket.py` 的既有失敗（base commit 就存在，與本專案無關）。
