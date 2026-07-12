# HANDOFF_P2 — 安全管線統一(REST 發訊息)
- 專案: BridgeUs backend refactor
- Part: 2 / 安全管線統一; Branch: refactor/m4-p2-message-pipeline; 日期: 2026-07-11
- 使用模型與 App: Claude Sonnet 5 @ Claude Code

## 本 Part 目標
修掉 `MatchingRoomMessagesView.post` 繞過黑名單過濾/情緒/embedding/廣播的問題，讓 REST 與 WS 兩條發訊息路徑共用同一個 service。

## 已完成
- 新建 `apps/matching/services/message_pipeline.py`：`post_match_message`(REST 用，黑名單過濾 → 情緒分析 → 建立訊息 → 廣播 → 排程 embedding/情緒寫回)與共用核心 `finalize_match_message`(建立 + 廣播，WS 端也走這個)。
- `MatchingRoomMessagesView.post` 改走 `post_match_message`；被黑名單攔截時回 400 + `BLOCKED_MESSAGE`(與 WS 端 `content_blocked` 系統提示同一份文字)。
- `MatchRoomConsumer._relay_and_persist` 改為呼叫 `finalize_match_message`(用 `database_sync_to_async` 包，因為內部用 `async_to_sync(channel_layer.group_send)`)；移除консumer 上原本重複的 `_create_message`/`_message_payload`。
- `_run_message_analysis`(embedding 寫回 + 情緒分數寫回 + topic/stance/stalemate 互動式建議)維持不動 — 這段是 WS 專屬的「有 socket 才能推建議」邏輯，見下方決策。
- 新增 2 個 REST 測試於 `api/tests.py::MatchingApiTests`：`test_room_message_post_blocks_blacklisted_content`、`test_room_message_post_schedules_embedding_and_broadcasts`。

## 修改/新增檔案
- `apps/matching/services/message_pipeline.py`(新增): 共用 pipeline — `post_match_message`(REST 全流程)、`finalize_match_message`(建立+廣播，REST/WS 共用)、`_schedule_embedding_and_emotion`(REST 專用的 embedding/情緒寫回)、`BLOCKED_MESSAGE` 常數。
- `backend/api/views.py`: `MatchingRoomMessagesView.post` 改走 `post_match_message`，移除直接 `MatchMessage.objects.create`。
- `backend/api/consumers.py`: `_relay_and_persist` 改呼叫 `finalize_match_message`；`_handle_ai_assisted_message` 的攔截訊息改引用 `BLOCKED_MESSAGE`；刪除已搬移的 `_create_message`/`_message_payload`。
- `backend/api/tests.py`: 新增 2 個 REST pipeline 測試。

## 影響模組與擁有者(給隊友判斷是否要跟進)
- m4: REST 發訊息(`POST /api/matching/rooms/{room_id}/messages/`)現在會執行黑名單過濾與情緒分析(當 `H_H_AI_ASSIST_ENABLED=true`)，被攔截時回 `400` 而非原本一律 `201`；前端若有直接呼叫此 API 需處理 400 情況 -> 需通知 陳彩希(前端)。
- 無其他跨模組影響(WS 對外行為/payload 格式未變)。

## 重要決策(選了什麼 / 為什麼 / 否決了什麼)
- **REST 端情緒攔截降級為「黑名單同步擋 + emotion 記分」，不做建議重寫互動**：因為 REST 是一次性 request/response，沒有像 WS 那樣的持久通道可以推送 `match_ai_suggestion` 再等使用者 accept/modify/ignore；否決方案是讓 REST 也开一個輪詢/長輪詢通道來模擬互動，判斷過度複雜且非本 Part 範圍。
- **`finalize_match_message`(建立+廣播)是 REST/WS 共用核心，但 `_run_message_analysis` 的 topic/stance/stalemate 互動建議留在 consumer 內**：因為這三項都是「算出結果後透過 `self.send()` 推回同一個 socket」的行為，REST 沒有 socket 可推，若硬塞共用會讓 service 認知一個它管不了的通道；REST 改用自己的 `_schedule_embedding_and_emotion`(只做 embedding + 情緒分數寫回，不含互動建議)。
- **`check_content_sync`/`analyze_emotion` 是否執行，仍以 `hh_ai_assist_enabled()` 為準**：與 WS 現有行為一致(flag 關閉時兩條路徑都不過濾/不記情緒分數)，避免 REST 與 WS 在 flag 關閉時行為不對稱。

## 測試
- 指令: `cd /Users/light/code/backend && uv run pytest api/tests.py api/tests_websocket.py -q`
- 結果:
  - `api/tests.py`: 68 passed(含本 Part 新增 2 個測試)。
  - `api/tests_websocket.py`: 10 個既有 WS 測試全部通過；pytest 額外回報 10 個 teardown-only `ERROR`(`test_bridgeus_test couldn't be flushed` / `being accessed by other users`)。這是 baseline 既有的 DB flush 併發問題 — 用 `git stash` 切回 baseline 重跑同一檔案，出現一模一樣的「10 passed, 10 errors」，確認與本 Part 改動無關，不算入驗收失敗。
  - 完整基線指令 `uv run pytest`(不帶檔名)只會收集 `apps/matching/tests/test_*.py` 這兩個檔案(50 個測試)，`tests.py`/`tests_websocket.py` 等檔名不符合 pytest 預設的 `test_*.py`/`*_test.py` 收集規則 — 這件事在 P0 就已如此，非本 Part 造成，但下一位 Agent 若要驗證「全測試綠」需明確帶檔名或調整 `pythonpath`/`python_files` 設定。

## 未完成 / 已知問題
- `chat/tests_ai_assist.py`、`chat/tests_consumer.py`、`chat/tests_drift.py`、`chat/tests_session.py`、`chat/tests_stalemate.py`、`chat/tests_topic.py` 在 baseline(`git stash` 驗證過)就已经因為 `from chat.models import Conversation/AISuggestion/...` (舊 `chat` app 模型已移除)而 collection 失敗 — 與本 Part 無關，未修，NEEDS_DECISION 留給後續 Part 或 P7 判斷是否還要保留這批舊測試。
- pytest 預設只收集 `test_*.py`/`*_test.py`，導致 `tests.py`/`tests_websocket.py` 等一大批既有測試不在 `uv run pytest` 預設範圍內；建議在 P3 或 P7 補上 `[tool.pytest.ini_options] python_files = ["test_*.py", "tests*.py"]` 之類的設定並重新核對「全測試綠」的真正涵蓋範圍。此為 NEEDS_DECISION(是否現在修 pytest 設定，還是留給 P7)。

## 風險與限制
- REST 端情緒分析(`analyze_emotion`)現在會在 `H_H_AI_ASSIST_ENABLED=true` 時對每則 REST 訊息做真實 transformer 推論(非 mock)；本地測試已確認可行(模型第一次載入約 100 秒，之後同進程內重複使用單例、速度正常)，但若前端有高頻 REST 發訊息情境，需注意這條路徑的延遲比純 DB 寫入高很多。
- `broadcast_message` 用 `get_channel_layer()`；若某環境未設定 `CHANNEL_LAYERS`(回傳 `None`)，會靜默跳過廣播 — 目前專案一定有設定(`InMemoryChannelLayer` 或 Redis)，只是留意這個防呆分支未來若換設定要重新確認。

## 給下一個 Agent
- 必讀: `apps/matching/services/message_pipeline.py`、本文件「重要決策」一節、`api/tests.py` 內兩個新測試
- 不需重看: `semantic_tree.py`、P1 的鎖設計文件(與本 Part 無交集)
- 下一步任務: 依 `EXECUTION_PLAN.md` Part 3 進行效能與 API 快修包(History N+1、serializer 白名單、WS payload 防禦、限流)；`api/consumers.py`/`api/views.py` 會再次被改動，注意先讀本文件確認 P2 已改的區段(`_relay_and_persist`、`MatchingRoomMessagesView.post`)避免衝突。
- 驗收條件: `cd backend && uv run pytest api/tests.py api/tests_websocket.py -q` 全綠(teardown-only 的 flush ERROR 可忽略，見上「測試」一節說明)。
- 建議 Context: 新開；帶入本文件 + Part 3 指定的程式碼區段；不帶 message_pipeline.py 實作細節(Part 3 與其無交集)。
- 是否需要 Review 本 Part: P7 中等(依 Review 策略總表)。
- 啟動 Prompt: 你是 BridgeUs backend refactor 的 Part 3 執行者。開啟 repo `/Users/light/code`，切到 branch `refactor/m4-p3-perf-api-fixes`(從當前 feat/Light 切出，注意 Part 3 必須排在 Part 2 併回之後)。先讀:(1) `backend/docs/refactor/handoffs/HANDOFF_P2.md`(只看測試指令) (2) `backend/docs/refactor/EXECUTION_PLAN.md` 的「Part 3」章節。你的任務: 依序修 History N+1、serializer 白名單、WS payload 防禦、限流四項，各自獨立 commit。完成後: 跑 `cd backend && uv run pytest api/tests.py api/tests_websocket.py -q`，摘要寫進 HANDOFF_P3，commit 並停止。
