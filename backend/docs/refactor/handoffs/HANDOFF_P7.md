# HANDOFF_P7 — 最終整合 Review 與結案
- 專案:BridgeUs backend refactor
- Part:7 / 最終整合 Review 與結案;Branch:`refactor/p7-integration`;日期:2026-07-12(兩輪,同日)
- 使用模型與 App:Claude Fable 5 @ Claude Code

## 本 Part 目標
把散落在各子分支的 P1~P6 整合成單一分支、解掉 Part 間衝突、逐行 review 高風險區、全回歸 + 冒煙、產出結案報告。

## 已完成(第一輪:P1~P5)
- 發現實際 repo 狀態與計畫不符(各 Part 未併回 feat/Light、P6 當時未執行),改為自建整合分支處理(詳見 CLOSEOUT.md §0)。
- 建 `refactor/p7-integration`(自 `refactor-baseline`),依序 `--no-ff` merge:P1(m5-p1-semantic-tree-lock)→ P2(m4-p2-message-pipeline)→ P3(m3-p3-api-quickfixes)→ P5(m3-p5-session-state,內含 P4)。
- 解 P1×P5 / P3×P5 衝突並修 4 個接縫問題(雙寫 stand-in 刪除、N+1 修復移植進 service、WS 測試 patch 目標、死 import)。
- 逐行 review P1 鎖語意(claim TTL / 兩段交易 / 衝突丟棄)與 P5 資料一致性(欄位所有權 / 鎖內合併 / read-through);P2 async/sync 邊界與 P3/P4 快掃。無 blocker。

## 已完成(第二輪:P6 補整合,本輪)
- P6 已在 `refactor/core-p6-cleanup` 補做完成(4 commits + HANDOFF_P6),但從 baseline 切出,沒看到 P1~P5 結構。merge 進 `refactor/p7-integration` 時衝突 3 檔(views/consumers/semantic_tree),採「P1~P5 結構為準、P6 語意重新套用」解法,並把 P6 收斂不到的重複碼落點補齊(dialogue_session/history/room_state/message_pipeline 四個 service 檔 + semantic_tree 的 cache-key 副本)。細節見 CLOSEOUT.md §2 第 5-6 點與 merge commit `merge: integrate P6 cleanup package into p7-integration`。
- 重新逐段 review P6×P1 接縫:兩條 analyze 路徑的鎖語意(claim→鎖外 LLM→驗證寫回→conflict 丟棄→cache invalidate)在 anchors 改為 strict lookup 後完整保留;anchors/descriptions 都在鎖交易內解析。
- ruff(F/E9)掃全部改動檔;全測試重跑 + 冒煙重跑,全綠(數字見 CLOSEOUT §1)。
- 更新 `CLOSEOUT.md`:主題 3/4/7 轉「已修」、主題 8 轉「部分修」,遺留優先序重排。

## 修改/新增檔案(第二輪)
- merge `refactor/core-p6-cleanup`(core/env.py、dialogue_topics accessors、Issue 刪除 + migration 0012、main.py 刪除、docs 對齊等,見 HANDOFF_P6)。
- `backend/api/services/dialogue_session.py`:改用 core.env 的 TTL 與 cache key(刪本地副本)。
- `backend/api/services/history.py`:root name 改走 `get_topic_title`(不再 fallback「核電」)。
- `backend/api/services/room_state.py`、`backend/apps/matching/services/message_pipeline.py`:匿名顯示名改用 core.env 常數。
- `backend/apps/matching/services/semantic_tree.py`:anchors import 自 dialogue_topics;刪本地 `_dialogue_session_cache_key` 改用 core.env。
- `backend/api/views.py`/`consumers.py`:丟棄 P6 的 baseline 形狀改動,移除死 import。
- `backend/api/serializers.py`:移除 baseline 既有的 `DiscomfortReport` 死 import。
- `backend/docs/refactor/CLOSEOUT.md`:更新為 P1~P6 全整合版。

## 影響模組與擁有者
- m3/m4/m5:整合不改各 Part 已記錄的對外行為;前端需知悉的變更以 HANDOFF_P2(REST 發訊息可回 400)與 HANDOFF_P3(serializer 欄位、405)為準 → 通知 陳彩希(前端)。
- **P6 行為變更**:查不到 topic config(title/anchors/descriptions)從默默 fallback 核能改成 raise `ValueError`;`/api/issues/` 端點已刪除(demo 死碼,前端 grep 無使用)→ 通知 陳彩希、Benjamin。
- 全體:`refactor/p7-integration` 尚未併回 `feat/Light`,併回屬人工 review 步驟 → Benjamin。

## 重要決策(選了什麼 / 為什麼 / 否決了什麼)
- 選擇自建整合分支而非停工等人:整合衝突正是 P7「各 Part 接縫」review 的主體,且 merge 完全可回退;否決「直接動 feat/Light」——計畫明訂併回前需人工 review。
- analyze view 採 P1 語意(view 零寫回)並刪 stand-in:P1 服務層已在鎖交易內寫回 + 刪 cache,view 再寫會以記憶體舊值蓋掉驗證過的結果。
- P6 併入採「結構以 P1~P5 為準、語意以 P6 為準」:P6 的目標是收斂重複碼與 strict topic lookup,落點在哪個檔案是手段不是目的;否決「保留 P6 的 baseline 形狀再另開 Part 搬」——會讓同一常數短暫存在兩處。
- consumers.py 不再 import core.env:P5 的 read-through 已讓 consumer 不直接摸 cache,P6 對 consumer 的 cache-key 改動整段丟棄(它改的程式碼已不存在)。

## 測試(P6 併入後全部重跑)
- `DB_ENGINE=sqlite uv run python manage.py test api --keepdb --noinput` → 78 OK
- `DB_ENGINE=sqlite uv run pytest api/tests_websocket.py` → 13 passed
- `DB_ENGINE=sqlite uv run pytest apps/matching -q` → 58 passed
- `DB_ENGINE=sqlite uv run pytest chat/tests_embedding.py chat/tests_warm_nlp_models.py` → 13 passed
- `uv run pytest`(baseline 指令)→ 58 passed;`manage.py check` 無 issue
- 冒煙:暫時性 e2e 測試(跑完已刪):建 session → reply(mock LLM)→ analyze(mock 分類器,`analysisStatus=ready`)→ history 列表(`message_count=2`)→ detail(2 則)。全通過。

## 未完成 / 已知問題
- **NEEDS_DECISION:配對 enqueue deadlock**(CODE_REVIEW P2,計畫從未分配)——上線前建議修,方向:統一鎖序或捕 OperationalError retry。
- **NEEDS_DECISION:chat/tests*.py 舊測試**(collection 失敗)與 pytest `python_files` 設定,兩項綁定。
- **NEEDS_DECISION:生產 DB 舊資料量測**(session_state.history 非空且無 turns 的 record 數,HANDOFF_P5 §8)。
- topic config 缺失現在會 raise(500):上線前可考慮 view 層轉 4xx,見 CLOSEOUT §4 第 4 點。
- 其餘未修小項見 CLOSEOUT §3(均為計畫未分配項)。

## 風險與限制
- 本次測試全在 `DB_ENGINE=sqlite`;P1 的 select_for_update 語意在 sqlite 上是弱化模擬,Postgres 上的真並發行為僅由單元測試近似覆蓋(HANDOFF_P1 已知限制,未變)。
- 整合分支只含 backend + 根目錄 CLAUDE.md;`feat/Light` 上若有其他人的新 commit,併回時需重新確認。
- P6 的 `frontend` 驗證(`npm run lint/build`)只在 P6 分支上跑過;整合分支未動 frontend,無需重跑。

## 給下一個 Agent
- 必讀:`CLOSEOUT.md`(尤其 §2、§4 優先序)、本檔
- 不需重看:各 Part 的設計文件與歷史對話(決策已沉淀在 CLOSEOUT 與各 HANDOFF)
- 下一步任務:人工 review `git diff refactor-baseline...refactor/p7-integration` 後併回 feat/Light,再走 feat/Light → dev PR
- 驗收條件:feat/Light 上重跑本檔「測試」節全部指令綠
- 建議 Context:新開;帶 CLOSEOUT.md;不帶其他
- 是否需要 Review 本 Part:兩輪整合衝突解法都寫在 merge commit message,人工併回時順看即可
- 啟動 Prompt:(併回)你是 BridgeUs backend refactor 的收尾執行者。開啟 repo /Users/light/code,先讀 backend/docs/refactor/CLOSEOUT.md。人工確認 `git diff refactor-baseline...refactor/p7-integration` 後,把 refactor/p7-integration 併回 feat/Light,重跑 HANDOFF_P7「測試」節全部指令確認綠,然後依團隊流程開 feat/Light → dev 的 PR。
