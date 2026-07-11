# HANDOFF_P1 — semantic_tree lock refactor
- 專案: BridgeUs backend refactor
- Part: 1 / 高風險並發修復(semantic_tree 鎖重構); Branch: refactor/m5-p1-semantic-tree-lock; 日期: 2026-07-11 23:17:17 CST
- 使用模型與 App: Codex (GPT-5) @ Codex App

## 本 Part 目標
把 semantic-tree analyze 的 LLM/urllib 呼叫移出 DB transaction，並用 row-local claim 防止同一 source id 被重複分析。

## 已完成
- `analyze_pending_room_messages` 改成交易 1 claim -> 鎖外分析 -> 交易 2 驗證/寫回；寫回只覆蓋目前 owner subtree。(commit: this commit)
- `analyze_pending_ai_conversations` 對 `DialogueSessionRecord` 套同一模式，鎖內重讀 DB record，只更新 `semantic_tree_state` 並在成功寫回後刪除 session cache。(commit: this commit)
- 新增 lock/concurrency 單元測試，覆蓋鎖外 LLM、active/expired claim、重複分析防護、寫回衝突丟棄、跨 owner 不互蓋、AI session 欄位不覆蓋與 cache invalidation。(commit: this commit)

## 修改/新增檔案
- `backend/apps/matching/services/semantic_tree.py`: 新增 `pendingClaims` + 150 秒 TTL，兩條 analyze 路徑改成三段式交易切分，LLM 呼叫集中在交易外 helper。
- `backend/api/views.py`: 移除 AI semantic-tree analyze 呼叫端的舊 write-through DB/cache 寫回，避免覆蓋 service 的鎖內寫回與 cache invalidation。
- `backend/api/tests.py`: 更新 AI semantic-tree analyze 成功後 cache 被刪除的既有預期。
- `backend/apps/matching/tests/test_semantic_tree_lock.py`: 新增 8 條並發/claim/鎖外分析測試。
- `backend/docs/refactor/handoffs/HANDOFF_P1.md`: 本交接文件。

## 影響模組與擁有者(給隊友判斷是否要跟進)
- m5: semantic-tree analyze 一致性與快取語意變更 -> 無跨模組影響；前端若看到 `analysisStatus="in_progress"` 或 `"conflict_retry"` 可直接重試/稍後重試。

## 重要決策(選了什麼 / 為什麼 / 否決了什麼)
- 選擇 owner-local `pendingClaims` 存在 semantic tree JSON 中，因為與 state 共用同一 row lock，避免 Redis/cache lock 的雙寫不一致。
- 選擇衝突時丟棄整批 LLM 結果並回 `conflict_retry`，因為 LLM items 是基於舊 treeData 算出，合併到新樹可能靜默損壞。
- 選擇 AI analyze 後刪除 cache 而非 write-through，因為 reply/restore 路徑可能同時更新 session state，read-through 下次可從 DB 重建。

## 測試
- 指令: `cd /Users/light/code/backend && uv run pytest apps/matching/tests/test_semantic_tree_lock.py`
- 結果: 通過 8 / 失敗 0；`8 passed in 25.10s`
- 指令: `cd /Users/light/code/backend && uv run pytest api/tests.py apps/matching/tests_semantic_tree_timeline.py apps/matching/tests_semantic_tree_node_creation_metadata.py apps/matching/tests_semantic_tree_node_birth.py apps/matching/tests_semantic_tree_session_timeline.py apps/matching/tests_semantic_tree_prompt.py apps/matching/tests/test_ccnd_snapshot_analysis.py apps/matching/tests/test_semantic_tree_lock.py`
- 結果: 通過 125 / 失敗 0；`125 passed, 62 warnings in 138.77s`
- 指令: `cd /Users/light/code/backend && uv run pytest`
- 結果: 通過 58 / 失敗 0；`58 passed, 1 warning in 27.74s`
- 指令: `cd /Users/light/code/backend && uv run python manage.py check`
- 結果: `System check identified no issues (0 silenced).`
- 驗證: `rg -n "transaction\\.atomic|analyze_text_for_tree|analyze_with_openai|urllib" backend/apps/matching/services/semantic_tree.py` 顯示 `analyze_text_for_tree` 呼叫在 helper 內，兩條 analyze 函式只在交易 1 與交易 2 之外呼叫該 helper。

## 未完成 / 已知問題
- 未做真 threading `TransactionTestCase`；本地 Postgres 測試庫在 `TransactionTestCase` flush 時會因 `chat_conversation -> auth_user` FK 未納入同批 truncate 而失敗。已以 claim/in_progress、expired claim、conflict discard、cross-owner preservation 等單元測試覆蓋並發語意。

## 風險與限制
- LLM helper 目前遇到單則分析例外會停止本批後續分析，已完成的結果仍寫回；這符合設計中的「第 3 則失敗時後 3 則仍 pending」語意。
- `analysisStatus` 新增 `"in_progress"` 與 `"conflict_retry"`；目前 serializer/API 可原樣回傳，前端若要更細 UI 可在後續處理。

## 給下一個 Agent
- 必讀: `backend/docs/refactor/handoffs/HANDOFF_P0.md`; `backend/docs/refactor/EXECUTION_PLAN.md` 的「Part 2」章節; Part 2 指定的 4 個程式碼區段。
- 不需重看: `backend/docs/refactor/CODE_REVIEW.md` 全文、semantic_tree 實作細節、Part 1 設計以外歷史對話。
- 下一步任務: 實作 Part 2 REST/WS 發訊息安全管線統一。
- 驗收條件: REST 與 WS 對同一輸入有相同過濾/攔截行為，既有 websocket 測試全綠。
- 建議 Context: 新開; 帶入 HANDOFF_P0 的測試指令、EXECUTION_PLAN Part 2、Part 2 指定檔案區段; 不帶 semantic_tree 內容。
- 是否需要 Review 本 Part: P7 逐行。
- 啟動 Prompt: 你是 BridgeUs backend refactor 的 Part 2 執行者。開啟 repo `/Users/light/code`，切到 branch `refactor/m5-p2-message-pipeline`。先讀:(1) `backend/docs/refactor/handoffs/HANDOFF_P0.md` 的測試指令 (2) `backend/docs/refactor/EXECUTION_PLAN.md` 的「Part 2」章節 (3) Part 2 指定的 `api/views.py`、`api/consumers.py`、`chat/services/filter.py` 區段。不要讀其他歷史文件或全 repo 掃描。你的任務: 統一 REST 與 WS 發訊息安全管線並補測試。驗收: REST 與 WS 對同一輸入有相同過濾/攔截行為，既有 websocket 測試全綠。完成後: 跑指定測試，把摘要寫進 `HANDOFF_P2.md`，commit 並停止。
