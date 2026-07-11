# HANDOFF_P0 — 基線建立
- 專案: BridgeUs backend refactor
- Part: 0 / 基線建立; Branch: feat/Light; 日期: 2026-07-11 22:51:35 CST
- 使用模型與 App: Codex (GPT-5) @ Codex App

## 本 Part 目標
建立後續 refactor 可重用的測試基線、handoff 模板與 baseline tag，且不改產品程式碼。

## 已完成
- 建立 `backend/docs/refactor/handoffs/` 與交接模板 `_TEMPLATE.md`，後續 Part 可直接複用。 (commit: this commit)
- 驗證 `git log feat/Light..origin/dev --oneline` 為空，確認 `origin/dev` 已併入 `feat/Light`。 (commit: this commit)
- 在 `backend/` 執行完整基線測試並整理可重用指令、結果、耗時、失敗清單與排除清單。 (commit: this commit)
- 記錄基線環境資訊: Python 版本與關鍵 env flags。 (commit: this commit)
- 於 `feat/Light` 建立 baseline tag `refactor-baseline` 指向本次 P0 commit。 (commit: this commit)

## 修改/新增檔案
- `backend/docs/refactor/handoffs/_TEMPLATE.md`: 將 `EXECUTION_PLAN.md` 第 7 節交接模板另存為獨立模板檔。
- `backend/docs/refactor/handoffs/HANDOFF_P0.md`: 記錄基線測試、環境資訊、git 驗證結果與下一棒啟動資訊。

## 影響模組與擁有者(給隊友判斷是否要跟進)
- m5: 建立 semantic-tree refactor 前置基線與交接文件入口 -> 需通知 Part 1a / 1b Agent 直接沿用本文件中的測試指令與 baseline tag; 無跨模組產品程式碼影響

## 重要決策(選了什麼 / 為什麼 / 否決了什麼)
- 選擇 `cd /Users/light/code/backend && uv run pytest` 作為後續 Part 的基線測試指令；因為此命令在當前 `feat/Light` 可直接收集並跑完整個 backend 可發現測試集。
- 不再追加 `python manage.py test`；因為 `pytest` 已成功，且本 Part 規則是先試 `pytest`，不行才 fallback 到 `manage.py test`。
- baseline tag `refactor-baseline` 直接打在本次 P0 文件 commit；這樣後續 Part 可用 tag 做 diff 與回歸，不會把 P0 文件變更算進後續改動。

## 測試
- 指令: `cd /Users/light/code/backend && uv run pytest`
- 結果: 通過 50 / 失敗 0；`pytest` 回報 `50 passed, 1 warning in 4.41s`，外層 wall clock 約 6.77s；失敗清單: 無；不可本地執行排除清單: 無(本次基線未觀察到缺 API key 或需額外模型下載而無法執行的測試子集)
- 備註: 1 則既有 warning 來自 `chromadb` telemetry 在 Python 3.14 的 `DeprecationWarning`，不影響本次 baseline 綠燈判定。

## 未完成 / 已知問題
- 無；`git log feat/Light..origin/dev --oneline` 為空，未觸發 `NEEDS_DECISION`。

## 風險與限制
- 本基線以目前 `backend/.env` 所對應的本地環境為準；若後續 Part 改變 DB 或 AI 相關 env，需先確認仍可重現本測試指令結果。
- 本次基線只記錄當前可被 `pytest` 發現並成功執行的 backend 測試；後續若新增需外部服務或大型模型前置的測試，應在各自 HANDOFF 另列新的排除清單。
- 目前環境值為: `Python 3.14.3`、`DB_ENGINE=postgres`、`H_H_AI_ASSIST_ENABLED=true`、`USE_REDIS_CHANNEL=false`。

## 給下一個 Agent
- 必讀: `backend/docs/refactor/handoffs/HANDOFF_P0.md`; `backend/docs/refactor/CODE_REVIEW.md` 的 `P1 — 鎖內呼叫 OpenAI,鎖可能被持有近 100 秒` 與 `P2 — AI session 語意樹分析無鎖 + 狀態 last-write-wins`; `backend/apps/matching/services/semantic_tree.py:1278-1417`
- 不需重看: 歷史對話、整個 repo、`CODE_REVIEW.md` 其他章節、與 Part 1a 無關的 `views.py`/frontend 檔案
- 下一步任務: 以 Part 1a 方式先產出 semantic-tree 鎖切分設計文件，明確寫出「鎖內只做什麼、鎖外只做什麼、寫回衝突如何處理」。
- 驗收條件: 產出 `backend/docs/refactor/p1_lock_design.md`；設計文件必須覆蓋 room 與 AI session 兩條 analyze 路徑，並明確記錄選定方案與否決選項。
- 建議 Context: 新開; 帶入 `HANDOFF_P0.md`、`CODE_REVIEW.md` 上述兩節、`semantic_tree.py:1278-1417`; 不帶其他 code review 章節、全 repo 掃描結果或歷史對話
- 是否需要 Review 本 Part: 否
- 啟動 Prompt: 你是 BridgeUs backend refactor 的 Part 1a 執行者。開啟 repo `/Users/light/code`，切到 branch `refactor/m5-p1-semantic-tree-lock`(從當前 `feat/Light` 切出；若已存在則沿用，同一分支供 1a/1b 共用)。先讀:(1) `backend/docs/refactor/handoffs/HANDOFF_P0.md` (2) `backend/docs/refactor/CODE_REVIEW.md` 的 `P1 — 鎖內呼叫 OpenAI,鎖可能被持有近 100 秒` 與 `P2 — AI session 語意樹分析無鎖 + 狀態 last-write-wins` (3) `backend/apps/matching/services/semantic_tree.py:1278-1417`。不要讀其他歷史文件或全 repo 掃描。你的任務: 產出 `backend/docs/refactor/p1_lock_design.md`，設計 `analyze_pending_room_messages` 與 `analyze_pending_ai_conversations` 的鎖切分方案，寫清楚交易 1 / 鎖外 LLM 呼叫 / 交易 2 寫回驗證流程，並記錄衝突處理規則與否決方案。驗收: 設計文件必須同時涵蓋 room 與 AI session，且後續實作者可直接依文件改碼。完成後: 不改產品程式碼；若時間不足，優先把未完成邊界寫回新的 handoff 或設計文件註記。
