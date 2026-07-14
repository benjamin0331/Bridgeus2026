# HANDOFF_P3

## 本 Part 完成內容
- `perf(m3): remove history list n-plus-one queries`
  - `HistoryConversationListView` 改為 summary 專用 queryset。
  - AI history 用 correlated subquery 拉 `message_count` / 最後一則 user prompt。
  - match history 用 `annotate(Count("messages"))` + 最後一則訊息子查詢。
  - 回傳 JSON 形狀不變；detail view 仍走全文 message payload。
- `fix(m3): restrict ai conversation api fields`
  - `AIConversationSerializer` 改白名單欄位，只輸出 `id/user/session_id/topic_id/user_prompt/ai_response/dialogue_phase/created_at`。
  - `embedding` 不再輸出。
  - `ai_response`、`dialogue_phase` 改 read-only；detail endpoint 改成 `RetrieveAPIView`，不再接受 `PUT/DELETE`。
- `fix(m3): harden websocket payload handling`
  - `DialogueStreamConsumer.receive()`、`MatchRoomConsumer.receive()` 都先檢查 `json.loads()` 結果是否為 `dict`。
  - 兩個 consumer 都新增 `content` 必須是 `str` 的檢查，像 `"[1]"` 或 `{"content": ["bad"]}` 只會被忽略，不會把 consumer 弄掛。
- `chore(core): add api throttle guards`
  - 新增 `backend/api/throttles.py`，定義 5 個 scope-specific throttle class。
  - `GuestLoginView` 掛 `AnonRateThrottle` 子類。
  - `DialogueSessionReplyView`、`DialogueSessionSemanticTreeAnalyzeView`、`HistoryConversationSemanticTreeAnalyzeView`、`MatchingRoomSemanticTreeAnalyzeView` 掛 `UserRateThrottle` 子類。
  - `REST_FRAMEWORK.DEFAULT_THROTTLE_RATES` 新增 env-driven rate 設定。

## Serializer / API 影響
- `/api/conversations/` 與 `/api/conversations/<pk>/` 不再回傳 `embedding`。
- `AIConversationDetail` 現在只有 `GET`；`PUT` / `DELETE` 會回 `405`。
- 前端 grep 結果: 目前 `frontend/src` 沒看到 AI conversation detail 的 `PUT/DELETE` 使用。

## Throttle 參數
- `DRF_THROTTLE_GUEST_LOGIN_RATE`，預設 `10/hour`
- `DRF_THROTTLE_DIALOGUE_REPLY_RATE`，預設 `30/min`
- `DRF_THROTTLE_DIALOGUE_ANALYZE_RATE`，預設 `6/min`
- `DRF_THROTTLE_HISTORY_ANALYZE_RATE`，預設 `6/min`
- `DRF_THROTTLE_MATCHING_ANALYZE_RATE`，預設 `6/min`

## 測試
- 指令: `cd /Users/light/code/backend && uv run pytest api/tests.py api/tests_websocket.py -q`
- 結果摘要:
  - `api/tests.py` 功能測試通過，包含本 Part 新增的 history constant-query、serializer/detail、websocket payload guard 對應 REST 驗收、以及 3 個 throttle 429 測試。
  - 整包結果為 `83 passed, 12 errors`。
  - 12 個 error 全部來自 `api/tests_websocket.py` teardown-only flush 問題，型態與 P2 handoff 相同：`chat_conversation` FK 參照 `auth_user`，導致 pytest teardown `TRUNCATE` 失敗；功能 assertions 本身沒有新增失敗。
- 另外驗證:
  - `HistoryConversationListView` 在 2 筆 AI history + 2 筆 match history 的情境下固定只打 `2` 個 SQL queries。
  - 新增 websocket 測試已確認 `"[1]"` 與非字串 `content` 會被忽略，後續正常 payload 仍可成功處理。

## 已知問題
- `api/tests_websocket.py` 的 teardown-only flush error 仍未處理，根因是 test DB flush 沒把 `chat_conversation` 一起 cascade truncate；這是 baseline 既有問題，不是 P3 新增。

## 下一步
- P4 若只做搬移，不需要改這份設定；但若要重跑 websocket 檔案的「全綠無 error」，得先決定是否修 test DB flush / legacy `chat_*` tables 的 teardown 問題。
