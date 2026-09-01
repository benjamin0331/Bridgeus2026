# Godot 裁決收尾強化（階段 5b）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修掉階段五留下的三個 P1 正確性缺陷與三個使用者看不到訊息的缺口，讓「房間作廢」這件事對兩位參與者都可見，且不會產生孤兒資料或破壞歷史憑證。

**Architecture:** 三個 P1 全部是「檢查與寫入之間有窗口」或「作用範圍過寬」的問題，修法都是收窄範圍或把驗證移進鎖內。作廢通知從「一次性、只有觸發者看得到」改成「持久化 + 逐人確認」——用既有的 `stats.binding` 存已通知名單，不新增 model。前端把通知從 `matchingError` 拆成獨立狀態，避免與既有的清空邏輯打架。

**Tech Stack:** Django + DRF + pytest、React + Vite。

**前置：階段五已完成**（`7f1ae97..0bdc0b1`）。

---

## 這一輪的來源

**三個 P1 來自外部獨立審查**，我已逐一對照程式碼驗證，全部成立：

1. **問卷送出與裁決之間的競態**（`GodotSurveyView` + `record_godot_survey`）
2. **退回一般模式會破壞歷史配對憑證**（`_godot_return_to_normal` 取消範圍過寬）
3. **作廢原因只有觸發裁決的那一個請求收得到**

**三個 UX 缺口來自階段五的最終總覽審查**：

4. 極端立場倖存者若立刻被重新配對，訊息被 `status !== 'matched'` 的渲染條件藏起來
5. 沒填問卷的那一方拿不到通知，問卷 Modal 不會關（與 P1-3 同源）
6. 409 回應裡的 `binding_cancel_reason` 是死負載，且 409 時不關問卷

**P1-2 比外部描述的更嚴重**：`match_pretest_state()` 是用「有沒有 MATCHED queue entry」當作
「這個人填過前測問卷」的憑證（spec §D5）。把該使用者該議題的**所有** MATCHED entry 改成
CANCELLED，等於讓**過去已完成房間**的前測憑證憑空消失——`match_pretest_state(舊房)` 之後會
謊報那個人沒填過問卷，直接影響研究資料的可解釋性。

---

## 執行前必讀

- 後端指令在 `backend/` 用 `uv run` 執行，**必須帶絕對路徑 cd**，否則 `uv run` 找不到虛擬環境並以 `Failed to spawn: pytest` 靜默失敗。
- **後端測試共用同一個 Postgres test database，一次只跑一個 pytest 程序。**
- ⚠️ **不要用 `pytest api`**——收集到 0 個測試、exit code 5，看起來像跑完沒事。一律指定檔名。
- **不要動 `godot/`**——這一輪完全不碰 Godot 端。
- 目前分支 `feat/Light`。**不要 amend、rebase、reset**——只新增新 commit。
- 註解與 docstring 用繁體中文。

### ⚠️ 測試的時鐘依賴（階段五踩過）

`tests_godot_adjudication.py` 的 `_godot_match` 預設期限已經是 **3600 秒**，不要改回 300。
測試裡的 `_submit_survey` 會實際跑 embedding 模型，機器負載高時單一測試可能耗掉數分鐘；
用 300 秒的話期限會在測試執行途中真的到期、裁決正確地把房作廢，測試卻是因為時鐘而不是
因為邏輯失敗。要驗逾時的測試一律自己傳負值。

## 檔案結構

| 檔案 | 動作 |
|---|---|
| `backend/apps/matching/services/matcher.py` | `record_godot_survey` 鎖內重驗；`_godot_return_to_normal` 收窄取消範圍並收 match；作廢時記錄待通知名單 |
| `backend/api/godot_binding.py` | 新增 `pending_cancel_notice_for()` / `mark_cancel_notice_seen()` |
| `backend/api/views.py` | `_godot_binding_fields` 改讀持久通知；`GodotSurveyView` 409 路徑 |
| `backend/api/tests_godot_adjudication.py` | 追加測試 |
| `frontend/src/pages/TopicChat.jsx` | 通知拆成獨立狀態、防禦性關閉問卷、409 處理 |

---

## Task 1: `record_godot_survey` 鎖內重新驗證

**Files:**
- Modify: `backend/apps/matching/services/matcher.py`
- Test: `backend/api/tests_godot_adjudication.py`

**問題**：`GodotSurveyView` 的裁決檢查在 transaction **外**。檢查通過之後、
`record_godot_survey` 取得鎖之前，清理指令或另一位的輪詢可能先把房間取消。`record_godot_survey`
拿到鎖後**完全沒有重驗 status**，照樣寫入 profile、MATCHED entry 與分數，然後回 200。

結果是孤兒狀態：**問卷顯示送出成功，但房間已作廢，而且這個人沒有被退回一般模式**——因為
`_godot_return_to_normal` 是在他的 MATCHED entry 存在之前跑的，當時 `pretest` 說他沒填完，
所以被跳過了。他手上有 `s_pre`、有 MATCHED entry 指向一間 CANCELLED 房、沒有 MATCHING entry，
完全卡死。

- [ ] **Step 1: 寫失敗的測試**

```python
@pytest.mark.django_db
def test_survey_write_is_rejected_if_room_cancelled_after_gate_check():
    """檢查與寫入之間的窗口：模擬「裁決檢查通過後，房間才被取消」。

    record_godot_survey 必須在鎖內重驗，否則會寫出「問卷成功但房已作廢、人也沒被
    重新分流」的孤兒狀態。
    """
    from apps.matching.services.matcher import record_godot_survey

    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-race-write")
    # 模擬窗口期間房間被別人取消（清理指令或另一位的輪詢）。
    DialogueMatch.objects.filter(pk=match.pk).update(
        status=DialogueMatch.Status.CANCELLED
    )

    result = record_godot_survey(
        user=user_a,
        match=match,           # 呼叫端手上仍是那份過期的 ACTIVE 快照
        topic_id=102,
        stance_score=6.0,
        stance_category="support",
        survey_answers=SUPPORT_ANSWERS,
        survey_open_answers={"Q9": "測試"},
    )

    assert result is None       # 寫不進去
    assert not MatchQueueEntry.objects.filter(user=user_a, match=match).exists()
    match.refresh_from_db()
    assert match.user_a_score is None


@pytest.mark.django_db
def test_survey_write_rejected_for_non_participant():
    """鎖內也要確認身份：呼叫端傳錯 match 不該寫得進去。"""
    from apps.matching.services.matcher import record_godot_survey

    user_a, user_b = _make_users()
    outsider = User.objects.create_user(username="uc", password="pw")
    match = _godot_match(user_a, user_b, room_id="room-race-outsider")

    result = record_godot_survey(
        user=outsider,
        match=match,
        topic_id=102,
        stance_score=6.0,
        stance_category="support",
        survey_answers=SUPPORT_ANSWERS,
        survey_open_answers={"Q9": "測試"},
    )

    assert result is None
    assert not MatchQueueEntry.objects.filter(user=outsider).exists()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_adjudication.py -q -k "race_write or race_outsider or non_participant"`
Expected: 兩個都失敗（目前會寫進去並回傳 match）。

- [ ] **Step 3: 實作**

`record_godot_survey` 取得鎖之後、任何寫入之前插入三道重驗，並把回傳型別改成
「成功回 match、拒絕回 None」：

```python
    with transaction.atomic():
        locked = DialogueMatch.objects.select_for_update().get(pk=match.pk)
        # 鎖內重驗：呼叫端的裁決檢查發生在 transaction 外，那之後到這裡取得鎖的
        # 窗口期間，清理指令或另一位的輪詢可能已經把房間取消。不重驗的話會寫出
        # 「問卷成功但房已作廢、人也沒被重新分流」的孤兒——因為退回一般模式是在
        # 這筆 MATCHED entry 存在之前跑的，當時會判定這個人沒填完而跳過他。
        if locked.status != DialogueMatch.Status.ACTIVE:
            return None
        if godot_binding_info(locked) is None:
            return None
        if user.id not in (locked.user_a_id, locked.user_b_id):
            return None
        profile, _ = UserStanceProfile.objects.update_or_create(
            ...（其餘不變）
```

`godot_binding_info` 要從 `api.godot_binding` import（該檔已有從 `api.*` import 的先例，
沿用既有風格；注意避免循環 import——`godot_binding.py` 只 import `api.models`，安全）。

- [ ] **Step 4: 呼叫端處理 None**

`backend/api/views.py` 的 `GodotSurveyView.post`，把 `record_godot_survey(...)` 的呼叫改成
檢查回傳值：

```python
        recorded = record_godot_survey(
            ...（參數不變）
        )
        if recorded is None:
            # 鎖內重驗擋下了——窗口期間房間被取消。回 409 跟前置檢查一致。
            return Response(
                {
                    "detail": "這個配對房間已結束。",
                    "binding_cancel_reason": binding_cancel_reason(match),
                },
                status=status.HTTP_409_CONFLICT,
            )
```

**注意**：`match` 這時是過期快照，`binding_cancel_reason(match)` 可能拿不到剛寫入的原因。
改成重讀：`binding_cancel_reason(DialogueMatch.objects.get(pk=match.pk))`。

- [ ] **Step 5: 跑測試確認通過並迴歸**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_adjudication.py -q`
Expected: 25 passed（既有 23 + 新增 2）。

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_survey.py -q`
Expected: 14 passed。**很重要**——`record_godot_survey` 的回傳型別變了，階段四的測試全會走到。

- [ ] **Step 6: Commit**

```bash
git add backend/apps/matching/services/matcher.py backend/api/views.py backend/api/tests_godot_adjudication.py
git commit -m "fix(m3): re-verify room state inside the survey write lock"
```

---

## Task 2: 收窄退回一般模式的取消範圍

**Files:**
- Modify: `backend/apps/matching/services/matcher.py`
- Test: `backend/api/tests_godot_adjudication.py`

**問題**：`_godot_return_to_normal` 目前把該使用者該議題的**所有** MATCHED entry 改成
CANCELLED，沒有限定是哪一間房。

而 `match_pretest_state()` 正是用「有沒有對應這間房的 MATCHED entry」當作「這個人填過前測
問卷」的憑證（spec §D5）。所以這會讓**過去已完成房間**的前測憑證憑空消失——之後
`match_pretest_state(舊房)` 會謊報那個人沒填過問卷。這不只是弄髒資料，是破壞研究資料的
可解釋性。

- [ ] **Step 1: 寫失敗的測試**

```python
@pytest.mark.django_db
def test_return_to_normal_does_not_touch_other_rooms_credentials():
    """退回一般模式只能取消「本次被裁決那間房」的 queue entry。

    MATCHED entry 是 match_pretest_state 判斷「填過前測問卷」的憑證，波及其他房
    等於讓歷史房間的前測紀錄憑空消失。
    """
    from api.godot_binding import match_pretest_state

    user_a, user_b = _make_users()
    # 同一位使用者、同一個議題的另一間（較早的）房，雙方都已填完。
    old_match = _godot_match(user_a, user_b, room_id="room-old-done")
    _submit_survey(user_a, SUPPORT_ANSWERS)
    _submit_survey(user_b, OPPOSE_ANSWERS)
    old_match.refresh_from_db()
    assert match_pretest_state(old_match)["both_done"] is True
    DialogueMatch.objects.filter(pk=old_match.pk).update(
        status=DialogueMatch.Status.CLOSED
    )

    # 新的一間房，A 填完、B 離開 → 裁決作廢 → A 退回一般模式。
    new_match = _godot_match(user_a, user_b, room_id="room-new-cancel")
    _submit_survey(user_a, SUPPORT_ANSWERS)
    new_match.refresh_from_db()
    _mark_seen(new_match, user_a, seconds_ago=1)
    _mark_seen(new_match, user_b, seconds_ago=GODOT_PRESENCE_TIMEOUT + 10)

    from apps.matching.services.matcher import resolve_godot_survey_gate
    resolve_godot_survey_gate(match=new_match)

    # 舊房的憑證必須毫髮無傷。
    old_match.refresh_from_db()
    assert match_pretest_state(old_match)["both_done"] is True
```

- [ ] **Step 2: 跑測試確認失敗**

Expected: `both_done` 變成 False（舊房憑證被波及）。

- [ ] **Step 3: 實作**

`_godot_return_to_normal` 的簽名加上 `match`，取消時限定該 match：

```python
def _godot_return_to_normal(*, user_id: int, topic_id: int, match) -> None:
    """把已填過問卷的參與者退回一般模式。

    §D3 在 Godot 房裡刻意不套用「中立→AI」分流（問卷是配對成立後才填的，這時
    判定某人該去 AI 會把已配好的兩人卡死）。但房間作廢之後那個顧慮消失了——
    這個人現在是單獨一個人，本來就該照他的立場走正常分流。
    """
    ...
    # 只取消「這間房」的 MATCHED entry。MATCHED entry 是 match_pretest_state
    # 判斷「填過前測問卷」的憑證，波及同使用者同議題的其他房，會讓那些房的
    # 前測紀錄憑空消失（歷史資料被改寫）。
    MatchQueueEntry.objects.filter(
        user_id=user_id,
        topic_id=topic_id,
        match=match,
        status=MatchQueueEntry.Status.MATCHED,
    ).update(status=MatchQueueEntry.Status.CANCELLED, cancelled_at=timezone.now())
```

呼叫端（`resolve_godot_survey_gate` 的退回迴圈）補上 `match=locked`。

- [ ] **Step 4-5: 測試通過並 commit**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_adjudication.py -q`
Expected: 26 passed。

```bash
git commit -m "fix(m3): scope return-to-normal cancellation to the adjudicated match"
```

---

## Task 3: 作廢通知持久化，兩位都收得到

**Files:**
- Modify: `backend/api/godot_binding.py`
- Modify: `backend/apps/matching/services/matcher.py`
- Modify: `backend/api/views.py`
- Test: `backend/api/tests_godot_adjudication.py`

**問題**：`binding_cancel_reason` 目前只有**觸發裁決的那一個請求**收得到。另一位下一次輪詢
時 `_get_active_match` 已經找不到 ACTIVE 房，拿不到原因；由清理指令取消時**兩邊都拿不到**。

而前端只在收到原因時才關閉問卷，所以沒收到的那位會保留問卷 Modal，並因為 `binding_source`
消失而停止輪詢——徹底卡住。

**設計**：作廢時在 `stats.binding` 記下待通知的使用者名單；`get_matching_state` 除了找 ACTIVE
房，也找「最近作廢、且這位使用者還沒被通知過」的 Godot 房，回報原因並把他標記為已通知。
逐人確認，所以兩位各自都會收到剛好一次，清理指令造成的作廢也涵蓋得到。不新增 model。

- [ ] **Step 1: 寫失敗的測試**

```python
@pytest.mark.django_db
def test_both_participants_each_receive_cancel_notice_once():
    """通知要逐人確認：兩位各自收到剛好一次，不是只有觸發裁決的那個人。"""
    user_a, user_b = _make_users()
    match = _godot_match(user_a, user_b, room_id="room-notice-both")
    _submit_survey(user_a, SUPPORT_ANSWERS)
    match.refresh_from_db()
    _mark_seen(match, user_a, seconds_ago=1)
    _mark_seen(match, user_b, seconds_ago=GODOT_PRESENCE_TIMEOUT + 10)

    client_a = APIClient()
    client_a.force_authenticate(user=user_a)
    client_b = APIClient()
    client_b.force_authenticate(user=user_b)

    # A 的輪詢觸發裁決並拿到原因。
    a_first = client_a.get("/api/matching/status/?topic_id=102")
    assert a_first.data["binding_cancel_reason"] == "godot_partner_left"
    # A 再次輪詢就不該重複收到。
    a_second = client_a.get("/api/matching/status/?topic_id=102")
    assert a_second.data["binding_cancel_reason"] is None

    # B 沒有觸發裁決，但仍然要收到一次。
    b_first = client_b.get("/api/matching/status/?topic_id=102")
    assert b_first.data["binding_cancel_reason"] == "godot_partner_left"
    b_second = client_b.get("/api/matching/status/?topic_id=102")
    assert b_second.data["binding_cancel_reason"] is None


@pytest.mark.django_db
def test_command_cancellation_still_notifies_both():
    """清理指令取消時沒有任何請求在場，兩位之後輪詢都要收得到。"""
    from django.core.management import call_command

    user_a, user_b = _make_users()
    match = _godot_match(
        user_a, user_b, room_id="room-notice-cmd", deadline_offset_seconds=-60
    )

    call_command("close_expired_godot_matches")
    match.refresh_from_db()
    assert match.status == DialogueMatch.Status.CANCELLED

    for user in (user_a, user_b):
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get("/api/matching/status/?topic_id=102")
        assert response.data["binding_cancel_reason"] == "godot_survey_timeout"
```

- [ ] **Step 2: 跑測試確認失敗**

- [ ] **Step 3: `godot_binding.py` 新增通知輔助**

```python
def pending_cancel_notice_for(match, user_id: int) -> str | None:
    """這位使用者還沒被告知過的作廢原因；沒有就回 None。

    作廢原因必須兩位都收得到，而觸發裁決的只會是其中一個請求（清理指令取消時
    甚至一個都沒有）。所以改成持久化 + 逐人確認：名單記在 stats.binding，
    每個人各自讀到一次。
    """
    binding = godot_binding_info(match)
    if binding is None:
        return None
    reason = binding.get("cancel_reason")
    if not reason:
        return None
    notified = binding.get("notified_user_ids") or []
    return None if user_id in notified else reason


def mark_cancel_notice_seen(match, user_id: int) -> None:
    """把這位使用者記成已通知（冪等）。"""
    binding = godot_binding_info(match)
    if binding is None or not binding.get("cancel_reason"):
        return
    notified = list(binding.get("notified_user_ids") or [])
    if user_id in notified:
        return
    notified.append(user_id)
    stats = dict(match.stats or {})
    new_binding = dict(stats.get(BINDING_STATS_KEY) or {})
    new_binding["notified_user_ids"] = notified
    stats[BINDING_STATS_KEY] = new_binding
    match.stats = stats
    match.save(update_fields=["stats"])
```

- [ ] **Step 4: `get_matching_state` 找「最近作廢待通知」的房**

在 `get_matching_state` 裡，`_get_active_match` 之後、若 `active_match` 為 None（或裁決後
變成非 ACTIVE）時，補一段查詢：

```python
    if godot_cancel_reason is None:
        # 沒有 ACTIVE 房時，看看有沒有「最近作廢、這位使用者還沒被告知」的 Godot 房。
        # 觸發裁決的只會是其中一個請求（清理指令取消時一個都沒有），另一位要靠
        # 這條路徑才收得到通知。
        recent_cancelled = (
            DialogueMatch.objects.filter(
                topic_id=topic_id,
                status=DialogueMatch.Status.CANCELLED,
            )
            .filter(Q(user_a_id=user.id) | Q(user_b_id=user.id))
            .order_by("-closed_at", "-id")
            .first()
        )
        if recent_cancelled is not None:
            pending = pending_cancel_notice_for(recent_cancelled, user.id)
            if pending:
                mark_cancel_notice_seen(recent_cancelled, user.id)
                godot_cancel_reason = pending
```

從 `api.godot_binding` import `pending_cancel_notice_for` / `mark_cancel_notice_seen`。

**同時**：原本「裁決當下捕捉原因」那段也要改成走同一套（捕捉後就 `mark_cancel_notice_seen`），
否則觸發者會收到兩次（一次來自捕捉、一次來自這條新路徑）。

- [ ] **Step 5: 測試通過並 commit**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_adjudication.py -q`
Expected: 28 passed。

Run: `cd /Users/light/code/backend && uv run pytest api/tests_mixed_entry.py -q`
Expected: 47 passed。**必跑**——又動到 `get_matching_state`。

```bash
git commit -m "feat(m3): persist godot cancel notice so both participants see it"
```

---

## Task 4: 前端把通知拆成獨立狀態並防禦性關閉問卷

**Files:**
- Modify: `frontend/src/pages/TopicChat.jsx`

**三個問題一起修：**

1. 通知目前塞在 `matchingError`，而渲染條件是
   `matchingError && !showSurvey && matchingState?.status !== 'matched'`
   （約 2607 行）。極端立場倖存者若被**立刻重新配對**（實驗場多人排隊時很可能），
   `status` 會是 `'matched'`，訊息就被藏起來——他直接掉進新房間，完全不知道原本的夥伴怎麼了。
2. 沒收到通知的那一方問卷 Modal 不會關（`setShowSurvey(false)` 只在收到原因時執行）。
3. 409 回應裡的 `binding_cancel_reason` 沒有被讀，且 409 時不關問卷。

- [ ] **Step 1: 獨立的通知狀態**

在其他 `useState` 旁邊加：

```javascript
  // Godot 房作廢的通知。刻意不重用 matchingError：那個會被輪詢裡的
  // 「status 不是 matching 就清空」邏輯抹掉，而且它的渲染條件排除了
  // status === 'matched'——倖存者若立刻被重新配對就永遠看不到訊息。
  const [bindingNotice, setBindingNotice] = useState('');
```

- [ ] **Step 2: 輪詢改設這個狀態**

把輪詢回呼裡既有的作廢處理（`if (response.data.binding_cancel_reason) {...}`）改成：

```javascript
        if (response.data.binding_cancel_reason) {
          setShowSurvey(false);
          setBindingNotice(
            response.data.binding_cancel_reason === 'godot_partner_left'
              ? '對方已退出配對，已為你轉回一般配對模式。'
              : '前測問卷逾時，已為你轉回一般配對模式。',
          );
        }
```

（不再呼叫 `setMatchingError`。）

- [ ] **Step 3: 防禦性關閉問卷**

即使通知因為任何原因沒收到，也不能讓人卡在一份送不出去的問卷前。加一個 effect：

```javascript
  useEffect(() => {
    // 保險絲：問卷開著、但後端已經不認為這是「Godot 待填問卷」狀態時就關掉。
    // 正常情況會由 binding_cancel_reason 的通知關閉；這裡是防止通知漏掉時
    // 使用者卡在一份送出去只會被 409 拒絕的問卷前。
    if (!showSurvey) return;
    if (!matchingState) return;
    if (matchingState.binding_source === 'godot') return;
    if (matchingState.status === 'idle') return;   // 一般入口本來就該顯示問卷
    setShowSurvey(false);
  }, [matchingState, showSurvey]);
```

**⚠️ 這個 effect 要小心不要誤關一般入口的問卷。** 一般入口在還沒送出時 `status` 是 `'idle'`，
上面的第四個條件就是為此。實作後請自己推演一次一般入口的流程確認沒被影響。

- [ ] **Step 4: 409 處理**

`handleSurveySubmit` 的 Godot 分支 `catch` 區塊改成：

```javascript
      } catch (error) {
        if (!isChatPageMountedRef.current) return;
        const reason = error?.response?.data?.binding_cancel_reason;
        if (error?.response?.status === 409) {
          // 房間在送出過程中被作廢了。關掉問卷並說明，不要停在一份送不出去的表單。
          setShowSurvey(false);
          setBindingNotice(
            reason === 'godot_partner_left'
              ? '對方已退出配對，已為你轉回一般配對模式。'
              : '前測問卷逾時，已為你轉回一般配對模式。',
          );
          return;
        }
        setMatchingError(
          error?.response?.data?.detail || '目前無法送出問卷，請稍後再試。',
        );
      }
```

- [ ] **Step 5: 渲染**

在 `matchingError` 那段渲染旁邊加上通知，**渲染條件不排除 `matched`**：

```jsx
              {bindingNotice && (
                <p className="matching-status-notice">{bindingNotice}</p>
              )}
```

`matching-status-notice` 的樣式加在 `TopicChat.css`，**照該檔既有的 `matching-status-error`
等類別的寫法**（顏色用資訊性的而非錯誤紅）。

- [ ] **Step 6: 自我檢查與 commit**

- `cd /Users/light/code/frontend && npm run lint 2>&1 | tail -5` 乾淨。
- 推演並寫進回報：(a) 極端倖存者被立刻重新配對 → `status === 'matched'` → 通知仍看得到；
  (b) 一般入口填問卷 → 防禦性 effect 不會誤關；(c) 409 → 問卷關閉且有訊息。

```bash
git commit -m "feat(m3): show godot cancellation notice independently of match status"
```

---

## 手動驗收清單（人類執行）

| # | 情境 | 預期 |
|---|---|---|
| 1 | A 填完、B 關分頁 | **兩人**都看得到「對方已退出配對…」（B 重新開頁面時也要看得到一次） |
| 2 | 承上，A 立場極端且佇列裡有其他人 | A 立刻被重新配對，**且仍然看得到說明訊息**（這是修正前看不到的情境） |
| 3 | 兩人都關分頁 → 跑清理指令 → 兩人各自重開頁面 | 兩人都收到「前測問卷逾時…」各一次 |
| 4 | 同一人同議題先完成一場對話，之後另一場被作廢 | 舊那場的前測憑證不受影響（DB 檢查：舊 match 的兩筆 MATCHED entry 仍在） |
| 5 | 問卷送出的瞬間房間剛好被作廢 | 回 409、問卷關閉、看得到說明，且 DB 沒有寫入該次 profile／分數 |
| 6 | 一般議題入口填問卷 | 完全不受影響，不會被防禦性 effect 誤關 |

## 已知的殘餘（不在這一輪）

- `semantic_distance` 在參與者開放題沒有 embedding 時仍寫 `0.0`（與真值不可區分）。一般配對
  流程也有同樣情形，屬既有行為。
- 清理指令對「從沒有人加入、也沒有 survey_deadline」的孤兒房會記成 `godot_partner_left`，
  與真正的「對方退出」無法區分。要區分的話需要第三個原因代碼。
- `api/tests.py::MatchingApiTests` 與 `api/tests_websocket.py` 的既有失敗（base commit 就存在，
  與本專案無關）。
