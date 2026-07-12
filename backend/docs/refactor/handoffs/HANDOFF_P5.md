# HANDOFF_P5 — AI Session 狀態單一化(turns 為權威)
- 專案: BridgeUs backend refactor
- Part: 5b / Session single-source-of-truth 實作; Branch: refactor/m3-p5-session-state; 日期: 2026-07-12
- 使用模型與 App: Claude Sonnet 5 @ Claude Code

## 本 Part 目標
讓 `AIConversation` turns 成為 AI session 對話狀態的唯一權威來源;`session_state` 不再持久化 history 全文;cache 變成純 read-through 投影(read-through populate、寫入者一律 invalidate)。

## 已完成
- **分支基底缺口**:`refactor/m3-p5-session-state` 原本直接從 baseline(`2f55d7b`)切出,沒有包含 P4 的 `api/services/dialogue_session.py`(`ls backend/api/services/` 只有 `__pycache__`)。設計文件 line 10-13 已預告這個情況並給了 fallback(函式仍在 `api/views.py`),但任務明確要求「改 `api/services/dialogue_session.py`」,因此選擇把 `refactor/m3-p4-service-extraction` merge 進本分支(merge commit,無衝突),取得設計文件實際引用的檔案路徑,而不是在 `views.py` 裡重新定位這些函式。
- `api/services/dialogue_session.py` 依 §3–§6 重寫:
  - `session_metadata(session, *, stance_drift)` — `to_dict()` 去 history、併入 stance_drift。
  - `build_session_state_from_turns`(原 `_rebuild_session_state_from_turns` 改名)+ legacy fallback 的 log warning(§8)。
  - `create_dialogue_session_record(...)` — 純 create,取代舊 `_persist_dialogue_session_record` 的 create 分支。
  - `update_session_metadata(*, session_id, user_id, metadata)` — `select_for_update` 鎖 row、鎖內重讀、依 §6 表格合併 `dialogue_phase`(重算)/`focus_signal_count`(max)/`user_reasoning_mode`(單向升級)後 `update_fields=["session_state","last_activity_at"]`。
  - `update_semantic_tree_state(...)` — 見下方「重要決策」。
  - `invalidate_dialogue_session_cache(session_id)`。
  - 刪除 `_persist_dialogue_session_record`。
- `api/views.py` 四個呼叫點全部改用新 helper:
  - `DialogueSessionCreateView` — 不再 `cache.set`,只呼叫 `create_dialogue_session_record`。
  - `DialogueSessionReplyView` — stance drift 改在覆寫 `session_record["session"]` **之前**計算(修掉 §1 的 direction-永遠-stable bug);成功/LLM 失敗兩條路徑都補上 `invalidate_dialogue_session_cache`。
  - `DialogueSessionSemanticTreeAnalyzeView` — 整包 persist/cache.set 改成只寫 `semantic_tree_state` + invalidate。
  - `HistoryConversationSemanticTreeAnalyzeView`(ai 分支) — `_cache_dialogue_session_record` 改 `cache.delete`(它原本就只寫 `semantic_tree_state` 單一欄位,不用改動 DB 寫入)。
- `api/consumers.py`(`DialogueStreamConsumer`)— 移除自建 `_session_cache_key` + 裸 `cache.get`,改用與 REST 共用的 read-through(順帶補上 cache 命中時的 user_id 檢查);stance drift 計算順序同 reply 修正;成功/串流失敗都 invalidate;`_create_ai_conversation` 回傳 `None` 時中止本輪(不再吞例外續聊,見 §7 WS 列的建議)。
- 測試:修正 7 個因 cache 行為改變而斷言過時的既有測試(create/analyze 後不再假設 cache 已填),新增 5 個測試對應 §10 驗收項(見下方「測試」)。

## 修改/新增檔案
- `api/services/dialogue_session.py`: create/update 拆分、鎖內合併、history 不再持久化
- `api/views.py`: 4 個 AI session view 改用新寫入路徑
- `api/consumers.py`: WS 路徑統一 read-through,加 turn-create 失敗中止
- `api/tests.py`: 更新 7 個既有斷言 + 新增 5 個測試
- `api/tests_websocket.py`: 補上真實 `DialogueSessionRecord` row(舊測試只 seed cache,新寫入路徑需要既有 DB row 才能 `select_for_update`)+ 新增重連測試

## 影響模組與擁有者
- M3(模組內部重構,對外 API response 形狀不變):無跨模組影響,前端(陳彩希)不需更動。
- 提醒未來做 P1(semantic_tree 鎖設計)的人:analyze 路徑目前用 `update_semantic_tree_state()` 當 stand-in(見下方「重要決策」),P1 進來後這段大概率要收斂或整個移除。

## 重要決策(選了什麼 / 為什麼 / 否決了什麼)
- 分支基底缺口用「merge P4 進 P5」解決,而非在 `views.py` 原地實作:更貼近設計文件實際引用的路徑,且 P4→P5 依序合併本來就是最終目標,不是繞路。
- P1 尚未落地(本分支 `apps/matching/services/semantic_tree.py` 的 `analyze_pending_ai_conversations` 沒有自己的 DB 交易,只在記憶體字典裡改 `session_record["semantic_tree"]`),但設計 §6 假設它存在(「P1 交易 2,自己的 select_for_update」)。新增 `update_semantic_tree_state()` 當 stand-in:只鎖 row、只寫 `semantic_tree_state` 單一欄位,刻意不碰 `semantic_tree.py` 內部,維持設計文件明訂的「5b 不做 semantic_tree.py 內部」邊界。這不是被否決的方案,是唯一能在不做 P1 的前提下,不讓 analyze 路徑退化回整包覆蓋 bug 的做法。
- reply×reply 合併測試直接呼叫 `update_session_metadata()` 模擬兩個 stale writer 交錯寫入,而非起真執行緒:sqlite 測試 DB 對真正併發寫入支援有限且容易 flaky,直接測合併函式本身的鎖內重讀+合併邏輯,更精準對應設計要驗證的東西。

## 測試
- 指令(沿用 HANDOFF_P4 記錄的驗證指令,補上本次新增的 apps/matching):
  ```bash
  DB_ENGINE=sqlite uv run python manage.py check
  DB_ENGINE=sqlite uv run python manage.py test api --keepdb --noinput
  DB_ENGINE=sqlite uv run pytest api/tests_websocket.py
  DB_ENGINE=sqlite uv run pytest apps/matching -q
  rg "from api.views import" backend/api/consumers.py
  ```
- 結果:全綠。`api` 69 tests(含 5 個本次新增)、`api/tests_websocket.py` 11 tests(含 1 個本次新增)、`apps/matching` 50 tests 皆通過。`rg` 無輸出。
- 已知不綠(沿用 HANDOFF_P4 記錄,與本 Part 無關,未嘗試修復):`chat/tests*.py` 在 collection 階段就因 `chat.models` 不再 export `Conversation`/`AISuggestion`/`StanceDrift` 而失敗。

### 對應 §10 驗收項的測試
- kill cache 後從 DB 完整恢復:`test_latest_session_restores_history_after_cache_loss`(既有)、`test_dialogue_websocket_restores_from_database_when_cache_is_empty`(新增,WS 版)。
- reply×analyze 並發不遺失 history:`test_reply_and_analyze_interleaving_does_not_lose_history_or_tree`(新增)。
- `session_state` 不再存 history 全文:散在多個測試的 `assertNotIn("history", session_state)`。
- reply×reply 併發 metadata 合併(focus_signal_count 不遺失、user_reasoning_mode 不回退):`test_concurrent_reply_metadata_writes_do_not_lose_updates`(新增)。
- 舊資料 fallback(turns 空、舊 history 非空可讀):`test_session_restore_falls_back_to_legacy_history_when_turns_are_empty`(新增)。
- stance_drift direction 不再永遠 stable(§1 bug 修復):`test_ai_reply_includes_session_stance_drift_value` 的第二輪 reply 斷言(既有測試改寫,用不同 embedding 讓 drift_value 真的變化,驗證 direction 正確算出 `"diverging"` 而非硬編碼 `"stable"`)。

## 未完成 / 已知問題
- `update_semantic_tree_state()` 是 P1 未落地前的 stand-in(見「重要決策」)。P1 完成後應檢查是否收斂成呼叫 P1 自己的交易函式,或整個移除讓 view 不再做任何持久化呼叫。**NEEDS_DECISION**:交給實作 P1 的人判斷。
- §8 要求的一次性量測「`session_state.history` 非空且無對應 `AIConversation` 的 record 數」本次未執行(需要生產/預發資料庫存取權,非本地開發環境可判斷)。**NEEDS_DECISION**:上線前找有資料庫存取權的人跑一次;若數量 > 0 需按 §8 討論回補規則。
- `_build_topic_config`(P4 遺留,非本次改動)內有兩段重複的 `infer_reasoning_mode` 呼叫,不影響行為只是多算一次,不在 P5 範圍,順手記錄但未動。

## 風險與限制
- `update_session_metadata` / `update_semantic_tree_state` 都用 `select_for_update()` 鎖同一個 `DialogueSessionRecord` row(鎖不同欄位),兩把鎖在 DB 層依序排隊,設計 §6 已討論過,非本次新增風險。
- `update_semantic_tree_state` 沒有 P1 未來會有的衝突偵測(`analyzedSourceIds` claim 等),目前只是簡單的鎖 row + 覆寫欄位;analyze×analyze 併發race 不在本 Part 驗收範圍內(§10 只要求 reply×analyze),留給 P1。

## 給下一個 Agent
- 必讀: 本檔(HANDOFF_P5.md);如需回顧決策脈絡才看 `p5_session_design.md`
- 不需重看: `CODE_REVIEW.md` 全文、`p1_lock_design.md`(除非要做 P1)
- 下一步任務: 視 `EXECUTION_PLAN.md` / `PLAN_TABLE.md` 排的下一個 Part(可能是 P1 semantic_tree 鎖設計落地,或 P6 clean-up——P6 範圍內含「舊 record 殘留 history key 由 P6 清理包擇機掃掉」,見設計 §8 結尾)
- 驗收條件: 全測試綠(見上「測試」章節指令);其餘同 §10,已有對應測試覆蓋
- 建議 Context: 新開;若要做 P1,帶入本 HANDOFF 的「未完成」章節即可,不需重跑 P5 的推導過程
- 是否需要 Review 本 Part: 快速(欄位所有權、鎖合併邏輯建議人工看一眼;其餘是機械替換)
- 啟動 Prompt: (下一個 Agent 視實際排程填寫)
