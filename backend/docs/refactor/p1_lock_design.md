# P1 鎖切分設計 — semantic_tree analyze 移出 LLM 呼叫

對象:`apps/matching/services/semantic_tree.py`

- `analyze_pending_room_messages`(match 版,現況:整段包在 `select_for_update` 交易內,LLM 最多 5 次 × 20s timeout,鎖可持有近 100 秒)
- `analyze_pending_ai_conversations`(AI session 版,現況:完全無鎖,整包 last-write-wins)

本文件只是設計,不含實作。實作者請嚴格照此文件,不要重新發明衝突策略。

---

## 1. 核心模式:三段式(交易1 → 鎖外 → 交易2)

兩個函式套用同一模式,差異只在鎖的目標 row 與寫回欄位(見 §3、§4)。

### 交易 1 — 挑選與宣告(claim),持鎖時間 = 純 DB 操作,毫秒級

1. `select_for_update` 鎖目標 row(match 版鎖 `DialogueMatch`;AI 版鎖 `DialogueSessionRecord`,**必須在鎖內重新從 DB 讀 record,不可信任呼叫端傳入的 dict**——它可能來自 cache 且已過期)。
2. 讀出 semantic tree state,計算 pending 清單(維持現有規則:排序、`analyzedSourceIds` 過濾、`semantic_tree_batch_size()` 上限)。
3. **額外排除「已被 claim 且未過期」的 source id**(claim 機制見 §2)。
4. 若 pending 非空:把本批 source id 寫入 claim(含 timestamp),存回 state,結束交易(commit 釋放鎖)。
5. 帶出鎖外所需的**快照**:
   - `owner_state["treeData"]` 的 deep copy(LLM 分析要以它為上下文,且鎖外會逐則累積修改)
   - 該 owner 的 `analyzedSourceIds` 基準集合(衝突偵測用)
   - pending 訊息的 id / 內容 / source 資訊(轉成純資料,不留 ORM 物件的惰性查詢)
   - `anchors` / `anchor_descriptions` / `topic_id`

`MissingOpenAIApiKey` 檢查移到**交易 1 之前**(它只讀 env 與 topic config,不需要鎖;現在放在鎖內純屬歷史包袱)。

### 鎖外 — LLM 分析,不持任何 DB 鎖、不在任何 `transaction.atomic()` 內

對每則 pending 訊息依序:

1. `analyze_text_for_tree(...)`,tree 參數傳**快照副本**。
2. `apply_analysis_items_to_tree` 套用到快照副本(逐則累積,維持現有「後面的訊息看得到前面結果」語意)。
3. 累積 `analysisHistory` entry 與已分析 source id 到本地清單,**不碰 DB**。

任何一則失敗(timeout、API error):已完成的結果照常進交易 2 寫回,失敗訊息留在 pending 下次再分析;寫回時一併清掉本批全部 claim(含失敗那些)。

驗收標準對應:此段完成後,`analyze_with_openai` / `analyze_text_for_tree` 呼叫點不得出現在任何 `transaction.atomic()` 區塊內(grep 可驗)。

### 交易 2 — 驗證與寫回,持鎖時間 = 純 DB 操作,毫秒級

1. 重新 `select_for_update` 同一 row,**重新讀出最新 state**(不是沿用交易 1 的物件)。
2. 衝突偵測:比較最新 state 中該 owner 的 `analyzedSourceIds` 與交易 1 帶出的基準集合。
   - **相等** → 無人動過,把快照 treeData 整段寫回 `state["participants"][owner_key]["treeData"]`,append 本批 history entries 與 source ids。
   - **不相等** → 有並發寫入,**丟棄本批全部 LLM 結果**(理由見 §5),不寫 treeData、不 append。
3. 無論成敗,清掉本批 claim。
4. **只覆寫自己 owner 的 subtree**:其他 participants 的 subtree 一律以「最新讀出的 state」為準原樣保留——這消除跨 owner 的 last-write-wins(兩位使用者各分析自己的樹,現況會整包互蓋)。
5. 存回 state,在同一交易內以最新 state 組 response payload,commit。

回傳:衝突丟棄時 `analyzed_count=0`,`analysis_status` 建議新增 `"conflict_retry"`(前端可直接再打一次;pending 判定會自動排除已被別人分析的 id,重打是冪等的)。若不想動 payload schema,回 `"ready"` + `analyzed_count=0` 也可接受,但要在 HANDOFF 註明。

---

## 2. Claim 機制(防止重複 LLM 花費)

寫回時去重只能防「重複 append」,防不了「兩個請求各燒一次 OpenAI」(CODE_REVIEW P2 明點的雙倍花費)。因此交易 1 要宣告:

- 存放位置:state JSON 內新增 `"pendingClaims": {"<sourceId>": "<ISO timestamp>"}`(per owner 或 state 頂層皆可,建議放 owner_state 內,與 `analyzedSourceIds` 同層)。
- TTL:**150 秒**(5 則 × 20s timeout + 緩衝)。挑 pending 時,claim 存在且未過期 → 跳過;已過期 → 視同無 claim(前一個 worker 掛了)。
- 生命週期:交易 1 寫入 → 交易 2 清除(成功、衝突、部分失敗都清)。process crash 由 TTL 兜底。
- 並發第二個請求的行為:交易 1 發現 pending 全被 claim → 直接回 `analysis_status="in_progress"`、`analyzed_count=0`,不打 LLM。

不用 cache lock / Redis lock 的原因:claim 與 state 同一個 row、同一把 `select_for_update` 保護,天然原子;引入第二個儲存介質會產生兩邊不一致的新問題。

---

## 3. match 版(`analyze_pending_room_messages`)的具體對應

- 鎖目標:`DialogueMatch.objects.select_for_update().get(pk=match.pk)`(兩個交易各鎖一次)。
- 衝突域:只有 `current_owner_key` 自己的 subtree(pending 本來就過濾 `owner_key == current_owner_key`)。
- 交易 2 寫回:只動 `state["participants"][current_owner_key]`,其餘 participants 與 state 頂層欄位以最新讀出值為準。
- `semantic_tree_payload(...)` 移到交易 2 內、寫回之後,用最新 state 產生。

## 4. AI session 版(`analyze_pending_ai_conversations`)的具體對應

- 鎖目標:`DialogueSessionRecord` row(`select_for_update`,以 session_id + user_id 查)。函式簽名建議從收 `session_record: dict` 改為收 `session_id` / `user_id`,record 一律鎖內讀——傳入 dict 就是 P2 race 的根源之一。
- 交易 1:鎖內讀 record → 取 `semantic_tree_state` → 挑 pending turns(`AIConversation` query 不需要鎖,turns 是 append-only 權威資料)→ 寫 claim → commit。
- 交易 2 寫回:**只更新 semantic-tree 相關欄位,不整包覆蓋 session record**。用 `update_fields=[...]`(獨立欄位時)或「鎖內重讀 record JSON → 只替換 semantic tree 那個 key → 存回」(state 內嵌時)。這樣 analyze 與 reply / WS stream 並發時,analyze 不會把別條路徑剛寫的對話 history 蓋掉。
- Cache:交易 2 commit 之後**刪除**該 session 的 cache key(invalidate),讓下次讀取 read-through 重建;不要 write-through——write-through 會和並發的 reply 路徑再賽跑一次。與 P5 的狀態單一化方向相容(P5 之後這裡可能整段簡化,本設計刻意不多做)。
- 衝突域:AI 版只有 `OWNER_AI_USER` 一個 owner,衝突偵測同樣比對 `analyzedSourceIds` 基準。

---

## 5. 寫回衝突處理:選「丟棄整批」,否決「合併」與「重算」

**選定:偵測到 `analyzedSourceIds` 基準不符 → 丟棄本批全部結果,清 claim,回報前端可重試。**

理由:

1. **正確性**:LLM 結果是「以交易 1 當下的 treeData 為上下文」算出來的 items。樹一旦被並發寫入改動,`apply_analysis_items_to_tree` 把舊上下文的 items 套到新樹上,可能產生重複節點或掛錯位置——CCND 靜默損壞比多花一次 API 錢嚴重得多,而且這種損壞事後幾乎無法察覺(這正是本 Part 被標為全案最高風險的原因)。
2. **發生率**:有了 §2 的 claim,同 owner 並發分析在交易 1 就會被擋下,能走到交易 2 才發現衝突的窗口極小(claim 過期後的殭屍 worker 寫回、或時鐘漂移),為極罕見路徑保留最保守行為是划算的。
3. **冪等重試便宜**:丟棄後訊息仍在 pending,前端重打一次即恢復,使用者感知只是「這批晚幾秒出現」。

**否決的選項:**

- **部分合併**(只丟已被別人分析的 id,其餘照套):否決。items 之間不獨立——同批第 3 則的分析上下文包含第 1、2 則套用後的樹,挑著套會產生「上下文裡有、樹上卻沒有」的引用;且合併邏輯本身難測試。
- **鎖內重算**(衝突時在交易 2 內重打 LLM):否決。這等於把 P1 的病灶(鎖內呼叫 OpenAI)從正常路徑搬到衝突路徑,鎖仍可能被持有 100 秒。
- **Celery / task queue**(把整個分析改成背景任務):否決。能解問題但代價不成比例——需要新增 worker + broker 基礎設施(目前部署是 2-4GB VPS,無 Redis worker 編制)、API 從同步回應改成 job polling 動 payload 契約、8 個月研究時程內維運成本過高。三段式在現有同步 API 形狀內就能達到相同的鎖語意。
- **樂觀鎖版本號欄位**(state 加 version,寫回比對):否決(作為主要機制)。`analyzedSourceIds` 基準比對已提供等價的衝突偵測,且粒度更準(只對自己 owner 的變動敏感,別的 owner 寫入不會誤判成衝突);version 欄位反而會把跨 owner 的無害並發也判成衝突。

---

## 6. 需新增的測試

放 `apps/matching/tests/test_semantic_tree_lock.py`(或併入既有 semantic tree 測試檔,實作者擇一)。LLM 一律 mock `analyze_text_for_tree`。

| # | 情境 | 斷言 |
|---|------|------|
| 1 | **LLM 不在交易內**:mock `analyze_text_for_tree`,side_effect 內檢查 `transaction.get_connection().in_atomic_block` | 為 `False`(兩個函式各一條) |
| 2 | **重複分析防護(match)**:第一次 analyze 完成後再跑一次 | 同 source id 不重複出現在 `analyzedSourceIds` / `analysisHistory`,第二次 LLM mock 呼叫次數為 0 |
| 3 | **claim 擋並發**:手動在 state 寫入未過期 claim 後呼叫 analyze | 回 `in_progress`、LLM 呼叫次數 0 |
| 4 | **claim 過期可重撿**:寫入過期 timestamp 的 claim 後呼叫 analyze | 該訊息被正常分析 |
| 5 | **寫回衝突丟棄**:mock LLM 的 side_effect 內(即鎖外階段)模擬另一寫入者更新該 owner 的 `analyzedSourceIds` 並存回 | 本批結果被丟棄:`analyzed_count=0`、無重複 append、claim 被清除 |
| 6 | **跨 owner 不互蓋(match)**:LLM 階段中模擬另一 owner 的 subtree 被更新 | 交易 2 寫回後,另一 owner 的更新仍在(且本批照常寫回——跨 owner 變動不算衝突) |
| 7 | **AI 版寫回不整包覆蓋**:analyze 的 LLM 階段中,session record 的其他欄位(如對話 history)被並發更新 | 交易 2 之後該更新仍在,semantic tree state 也正確寫入 |
| 8 | **AI 版 cache 失效**:analyze 成功後 | 該 session cache key 已被刪除(下次讀取走 DB) |
| 9 | **部分失敗**:5 則中第 3 則 LLM 拋例外 | 前 2 則寫回、`analyzed_count=2`,後 3 則仍 pending,全部 claim 已清 |
| 10 | (選配)**真並發**:`TransactionTestCase` + threading,兩執行緒同時 analyze 同一 match | LLM 總呼叫次數 = pending 數(不加倍)、無重複 append |

#10 若 CI 環境(sqlite)不支援 `select_for_update` 語意,允許以 #2+#3+#5 的單元近似替代,但要在 HANDOFF_P1 註明。

---

## 7. 實作者注意事項

- 不改 `semantic_tree_batch_size`、pending 排序規則、payload 對外形狀(除了可能新增的 `analysis_status` 值,見 §1)。
- 交易 1 帶出的訊息內容必須物化成純資料;鎖外禁止觸發任何 ORM 惰性查詢。
- `views.py` 的 4 個呼叫端簽名盡量不動;AI 版若改收 `session_id`,呼叫端同步微調並記入 HANDOFF_P1。
