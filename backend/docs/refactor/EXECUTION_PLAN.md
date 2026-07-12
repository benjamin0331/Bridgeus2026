# Take A Bridge Backend Refactor — Agent 詳細執行計畫

> 本文件設計為「可直接貼給 Codex App 或 Claude App 的 Agent」使用。
> 搭配文件:`Backend_CodeReview_Refactor準備報告.md`(問題來源)、`Refactor_計畫總表.md`(人類閱讀版)。
> 交接文件一律寫在 repo 內:`backend/docs/refactor/handoffs/HANDOFF_P{n}.md`(目錄不存在則建立)。
> **決策:交接文件跟著 commit 進 repo**——好處是決策留痕、PR reviewer 看得到、任何 App 的 Agent 都讀得到;代價是會出現在 PR diff 裡。若團隊不想讓它進 dev,替代方案:把 handoffs/ 加進 .gitignore,改放本機並於每個 Part 啟動 Prompt 中附上絕對路徑。二選一後全程一致,不要混用。

---

## 0. 核心規劃假設(執行前請確認,未確認前視為假設)

| 編號 | 假設 | 若不成立的影響 |
|---|---|---|
| A1 | GPT-5.4 = 低成本高額度;GPT-5.5 = 中;GPT-5.6 = 高階、Plus 額度最緊 | 模型分配表需按實際額度對調 |
| A2 | Claude App Plus 額度:Haiku 4.5 最寬 > Sonnet 5 > Opus 4.8 ≈ Fable 5 最緊 | 同上 |
| A3 | 兩個 App 都能開啟本機 repo、執行測試(`pytest` / `manage.py test`) | 若某 App 不能跑測試,測試工作全移到能跑的那邊 |
| A4 | **在原 repo(/Users/light/code)內就地 refactor,不開新專案。** ⚠️ feat/Light 與 dev **已分岔**(Light 領先 ~30 commit 功能work、dev 領先 2 commit,且 reasoning-mode 有重疊)。**被 review 的程式碼在 feat/Light 上,dev 尚無**,所以 refactor baseline 必須是 feat/Light,不是 dev。見「Git 工作流」節 | — |
| A7 | **前置(人工):把 dev 併進 feat/Light、解決 reasoning-mode 重疊、確認測試綠**,再開始 P0。此步需領域判斷,不交 Agent | 若無法先併 dev:以現況 Light 為 baseline,把「與 dev 調和」推遲到 P7 前做——但功能衝突會和 refactor 衝突混在一起,較難 |
| A5 | 測試套件目前是綠的(P0 會驗證) | 若本來就有紅測試,先記錄為既有問題,不算入各 Part 驗收 |
| A6 | 團隊其他成員近期不會大改 `api/views.py`、`semantic_tree.py` | 若會,P4 需要先協調凍結窗口 |

---

## 0.5 Git 工作流(所有 Part 共用)

**baseline**:`feat/Light`(前置步驟已把 dev 併入,故 Light ⊇ dev)。P0 在此打 tag `refactor-baseline`。

**每個 Part 的流程**(例外:P0 只產出文件與 tag,直接在 feat/Light 上 commit,是唯一不開子分支的 Part):
1. 從**當前** feat/Light 切子分支:`refactor/m{模組}-p{n}-{slug}`(如 `refactor/m5-p1-semantic-tree-lock`)。
   - 「當前」= 已含前面所有已併回的 Part。這自然滿足依賴順序(P4 切分支時 Light 已有 P1~P3)。
   - P1 ∥ P2:兩者都從同一個 baseline Light 切,檔案無交集,兩邊併回都乾淨。
2. Agent 在子分支做事、commit(Conventional Commits,scope=模組代號)、寫 HANDOFF。
3. **你 review 子分支的 diff**:`git diff feat/Light...refactor/m5-p1-...`(三個點,只顯示這個 Part 的改動)。
4. review 通過 → 併回 feat/Light(`--no-ff` 保留 Part 邊界);不通過 → 留在子分支修,Light 不動。
5. 全部 Part 併回後 → 最終 PR:`feat/Light → dev`(可依模組拆成數個 PR 讓對應 owner review)。

**回退**:壞掉的 Part = 不併回,或 `git revert` 該 merge commit。每個 Part 獨立,回退不影響其他。

**review 定位三件套**(你和隊友都靠這個,不必讀全 repo):
- 子分支 diff = 聚焦單一 Part
- HANDOFF「修改/新增檔案」= 逐檔一句話
- commit scope + HANDOFF「影響模組與擁有者」= 誰該跟進

---

## 1. 全域規則(每個 Agent 都必須遵守)

1. **開工條件**:讀完(a)本計畫中自己的 Part 章節、(b)前一個 Part 的 HANDOFF 文件、(c)Part 指定的「執行前讀取清單」。**禁止**回頭讀完整歷史對話或全 repo 掃描。
2. **完工條件**:驗收標準全過 + 交接文件寫完 + commit(遵守 repo 的 Conventional Commits,scope 用 m 模組代號)。
3. **交接文件**:一律用第 7 節的模板,存 `backend/docs/refactor/handoffs/HANDOFF_P{n}.md`。
4. **卡住就停**:遇到計畫未涵蓋的架構決策,不要自行發明——把問題寫進交接文件的「已知問題」,標記 `NEEDS_DECISION`,結束本 Part。
5. **額度警戒**:感覺額度快用完(回應變慢、被限流、剩餘對話數提示)時,立刻停止實作,先寫交接文件。寫交接文件的優先權永遠高於多修一個檔案。
6. **測試紀律**:每個 Part 結束前跑該 Part 指定的測試指令,把輸出貼進交接文件(裁剪到失敗摘要即可,不貼全文)。

---

## 2. Part 拆分(共 8 個 Part)

### Part 0 — 基線建立

- **目標**:確認測試基線、建立交接目錄,讓後續 Part 有可信的「改壞了沒」判準。
- **任務**:
  1. 建 `backend/docs/refactor/handoffs/` 目錄,放入交接模板(第 7 節)。
  2. 跑完整測試,記錄通過/失敗清單與執行時間。
  3. 記錄環境資訊(Python 版本、DB engine、關鍵 env flags:`H_H_AI_ASSIST_ENABLED`、`USE_REDIS_CHANNEL`)。
  4. 確認前置步驟(人工把 dev 併進 Light)已完成:`git log feat/Light..origin/dev --oneline` 應為空(dev 已全在 Light)。若非空,停止並在 HANDOFF 標 NEEDS_DECISION,請人先併 dev。
  5. 在 feat/Light 打 tag `refactor-baseline`。產出 HANDOFF_P0。
- **執行前讀取**:`Backend_CodeReview_Refactor準備報告.md` 第五節(執行順序)即可,**不需要**讀程式碼。
- **前置(人工,非 Agent)**:先把 `origin/dev` 併進 `feat/Light`,解決 reasoning-mode 重疊,確認測試綠(見假設 A7)。P0 只驗證此步已完成。
- **預期輸出**:HANDOFF_P0.md(含基線測試結果)。
- **驗收標準**:測試指令可重現;失敗測試(若有)已標記為既有問題。
- **App/模型**:Codex App / GPT-5.4(備用:Claude App / Haiku 4.5)。
- **選擇原因**:純執行與記錄,零推理需求,用最省額度的模型。
- **用量等級**:低。
- **交接內容**:基線測試清單、測試指令、環境資訊。
- **風險**:測試依賴外部服務(OpenAI/Anthropic key、torch 模型下載)而無法本地全跑 → 標記「不可本地執行的測試子集」,後續 Part 驗收排除該子集。
- **Context**:**新 Context**。不帶任何歷史;只帶本 Part 章節。
- **Review**:不需要 Review 任何東西(這是第一棒)。

---

### Part 1 — 高風險並發修復(semantic_tree 鎖重構)

- **目標**:修掉報告 P1「鎖內呼叫 OpenAI」與 P2「AI session 分析無鎖 + last-write-wins」兩個同領域問題。
- **任務**:
  1. `analyze_pending_room_messages`:改為「交易1(select_for_update)挑 pending + 讀樹快照 → 鎖外呼叫 LLM → 交易2(重新加鎖)驗證 `analyzedSourceIds` 未變後寫回;有變則丟棄或重算」。
  2. `analyze_pending_ai_conversations`:對 `DialogueSessionRecord` 加同等鎖(`select_for_update`),套用同一個「鎖外呼叫」模式;寫回改為只更新 `semantic_tree_state` 相關欄位,不整包覆蓋 session。
  3. 補並發防護測試:模擬兩個並發 analyze(可用 threading + `TransactionTestCase`,或以「第二次呼叫看到已分析 id 後跳過」的單元測試近似)。
- **執行前讀取**:報告的「P1 — 鎖內呼叫 OpenAI」與「P2 — AI session 語意樹分析無鎖」兩節(⚠️ 報告內 P1/P2 編號有多個,以標題為準);`apps/matching/services/semantic_tree.py` 的 1278–1417 行;`api/views.py` 中呼叫 analyze 的 4 個 view(搜 `analyze_pending`);HANDOFF_P0。
- **預期輸出**:修改後的 semantic_tree.py、新測試、HANDOFF_P1。
- **驗收標準**:
  - 交易內不再出現任何 `urllib`/OpenAI 呼叫(grep 可驗證:`analyze_with_openai` 不在 `transaction.atomic()` 區塊內)。
  - 既有 semantic tree 測試全綠 + 新並發測試綠。
  - 重複分析防護:同一 source id 不會被 append 兩次。
- **App/模型**:
  - **設計(30 分鐘內的小任務)**:Claude App / **Fable 5**(或 Opus 4.8)——只給它:報告兩節 + semantic_tree 相關 140 行程式碼,要求輸出「鎖切分方案 + 寫回衝突處理規則」一頁,存 `backend/docs/refactor/p1_lock_design.md`,不寫程式。
  - **實作**:Codex App / GPT-5.5(備用:Claude App / Sonnet 5)。
- **選擇原因**:鎖語意設計是全案最容易做錯且錯了最難察覺的部分,值得用最強模型但限制在小 context;實作照方案做即可,中階模型足夠。
- **用量等級**:設計=高(但量小)、實作=中。
- **交接內容**:鎖切分方案全文(含「為什麼不用 celery/task queue」等否決選項)、衝突處理規則、新測試位置。
- **風險**:寫回衝突策略(丟棄 vs 重算)影響使用者體驗 → 方案文件必須寫明選了哪個與原因;若設計階段 Fable 額度不足,降級 Opus 4.8,再不足用 GPT-5.6。
- **Context**:**兩個新 Context**(設計一個、實作一個)。設計 context 只帶:報告兩節 + 140 行程式碼。實作 context 帶:設計方案 + 目標檔案 + HANDOFF_P0 的測試指令。**不帶**:code review 的其他章節、任何 views.py 內容。
- **Review**:實作 Agent 開工前**只確認設計方案文件**(快速檢查,10 分鐘內),不重新分析問題;完工後由 P7 的高階模型做最終 review,本 Part 不另外安排。

---

### Part 2 — 安全管線統一(REST 發訊息)

- **目標**:修掉報告 P1「REST 發訊息繞過過濾/情緒/embedding/廣播」。
- **任務**:
  1. 新建 `apps/matching/services/message_pipeline.py`(或併入 matcher.py,擇一並記錄原因):`post_match_message(match, sender, content)` 同步版,內含黑名單過濾 → 情緒分析 → 建立 MatchMessage → channel layer `group_send` → 排程 embedding/分析。
  2. `MatchingRoomMessagesView.post` 改走此 service;被過濾時回 400 與相同的中文錯誤訊息。
  3. `consumers.py` 的 `_relay_and_persist` 改為呼叫同一 service 的核心(注意 async/sync 邊界:consumer 端用 `database_sync_to_async` 包)。
  4. 測試:REST 發含黑名單詞的訊息被擋;REST 發正常訊息後,訊息有 embedding 排程且 group_send 有被呼叫(mock channel layer)。
- **執行前讀取**:報告「P1 — REST 發訊息繞過整條安全管線」節;`api/views.py:1396-1433`;`api/consumers.py` 的 `_handle_ai_assisted_message`/`_relay_and_persist`/`_run_message_analysis`;`chat/services/filter.py`;**HANDOFF_P0(測試指令)**——P2 與 P1 並行,不依賴 HANDOFF_P1;若 P1 已完成,可順帶看其「已知問題」。
- **預期輸出**:新 service、兩端改接、測試、HANDOFF_P2。
- **驗收標準**:REST 與 WS 兩條路徑對同一輸入產生相同的過濾/攔截行為;既有 websocket 測試全綠。
- **App/模型**:Claude App / Sonnet 5(備用:Codex App / GPT-5.5)。
- **選擇原因**:牽涉 Django Channels async/sync 邊界,Claude 系對此語意較穩;複雜度中等,不需頂級模型。
- **用量等級**:中。
- **交接內容**:service 介面簽名、REST/WS 各自呼叫方式、情緒攔截在 REST 端的行為決策(建議:REST 端只做黑名單同步擋 + emotion 記分,不做「建議重寫」互動——因為 REST 沒有互動通道;此決策要寫進交接)。
- **風險**:REST 端無法重現 WS 的「建議重寫」互動流 → 明確降級為「擋下+訊息」,寫入決策紀錄;若團隊之後要 REST 完整互動,另開需求。
- **Context**:**新 Context**。帶:HANDOFF_P0 的測試指令、上列 4 個檔案的指定區段。**不帶**:semantic_tree 的任何內容(P1 與 P2 無程式碼交集)。
- **Review**:開工前**只讀 HANDOFF_P0**(不依賴、不 review P1——兩者無交集且可並行);不需高階模型 review,留給 P7。

---

### Part 3 — 效能與 API 快修包

- **目標**:一次修掉四個低風險、彼此獨立的小問題,共用一個 Context 省額度。
- **任務**(依序,每項獨立 commit):
  1. **History N+1**:`HistoryConversationListView` 改 `annotate(Count)` + 最後一則訊息子查詢;保持回傳 JSON 形狀不變。
  2. **Serializer 白名單**:`AIConversationSerializer` 改白名單 fields,`embedding` 不輸出、分析欄位 read-only;`AIConversationDetail` 移除 Update/Destroy(改 `RetrieveAPIView`)——若前端有用到 PUT/DELETE,先 grep frontend 確認,有用到就只做 read-only 欄位。
  3. **WS payload 防禦**:兩個 consumer 的 `receive` 加 `isinstance(data, dict)` 與 content 型別檢查。
  4. **限流**:DRF throttle 設定——`GuestLoginView` 加 AnonRateThrottle(建議 10/hour)、`DialogueSessionReplyView` 與三個 semantic-tree analyze view 加 UserRateThrottle(建議 30/min、analyze 6/min);數值以 env 可調。
- **執行前讀取**:報告對應 4 節;`api/views.py` 指定 view、`api/serializers.py:7-11`、`api/consumers.py:102-118 與 330-352`、`take_a_bridge/settings.py` REST_FRAMEWORK 段;HANDOFF_P2(只看測試指令)。
- **預期輸出**:4 個 commit、每項至少 1 個測試、HANDOFF_P3。
- **驗收標準**:History 列表在 assertNumQueries 下查詢數為常數(不隨紀錄數線性成長);embedding 不再出現在 list API 回應;`[1]` payload 不再讓 consumer 拋例外;throttle 觸發回 429。
- **App/模型**:Codex App / GPT-5.4(備用:Claude App / Haiku 4.5)。
- **選擇原因**:四項都是模式明確的機械修改,低階模型 + 明確指令即可,是全案最該省錢的一段。
- **用量等級**:低。
- **交接內容**:throttle 參數與 env 名稱、serializer 欄位變更清單(前端可能受影響的欄位)、assertNumQueries 基準值。
- **風險**:serializer 改動影響前端 → 交接文件列出「回應中移除的欄位」,由人類通知前端;若 grep 發現前端依賴 PUT,降級為只鎖欄位。
- **Context**:**新 Context**,四項共用(它們檔案不重疊、單項都很小)。帶:報告 4 節 + 指定程式碼區段。**不帶**:P1/P2 的任何實作細節。
- **Review**:不 review 前面 Part(無交集);只確認 HANDOFF_P2 的測試指令可用。

---

### Part 4 — 服務層抽取(views → services)

- **目標**:把 `api/views.py` 約 40 個 `_helper` 抽成 service 模組,消除 consumers→views 反向依賴,為 Part 5 鋪路。
- **任務**:
  1. **先產搬移清單**(高階模型、小 context):把 views.py 的 helper 依職責分組 → `api/services/dialogue_session.py`(cache/persist/restore/payload)、`api/services/stance_scoring.py`(計分/分類/開放題)、`api/services/history.py`(history 組裝)、`api/services/room_state.py`(presence/touch)。輸出「函式 → 目標模組」對照表 + import 更新點清單。
  2. **機械搬移**(低階模型):照清單搬,**不改任何函式內容**,只改 import;consumers.py 改 import service。
  3. 全測試回歸。
- **執行前讀取**:搬移清單設計者讀:`api/views.py` 全檔(只此一次!)+ 報告主題 1;搬移執行者讀:對照表 + HANDOFF_P3 測試指令,**不需讀報告**。
- **預期輸出**:services 模組、瘦身後的 views.py(目標 <600 行)、對照表存 `backend/docs/refactor/p4_move_map.md`、HANDOFF_P4。
- **驗收標準**:全測試綠;`grep "from api.views import" backend/api/consumers.py` 無結果;views.py 不再定義被 consumers 使用的函式;無行為變更(diff 只有搬移與 import)。
- **App/模型**:
  - 搬移清單:Claude App / **Opus 4.8**(備用:GPT-5.6)——這是分組判斷,不需要 Fable。
  - 機械搬移:Codex App / GPT-5.4(備用:Haiku 4.5)。Codex 對「大量檔案機械性搬移 + 全 repo import 修正」效率高。
- **用量等級**:清單=中(量小)、搬移=低~中(量大但便宜)。
- **交接內容**:對照表、新模組公開介面清單、「哪些 helper 刻意沒搬與原因」。
- **風險**:搬移中發現隱藏循環 import → 規則:遇到就把該函式留在原地並在交接標記,不要現場發明新抽象;Context 快滿 → 按模組分批 commit,交接文件記錄「已搬 / 未搬」邊界。
- **Context**:**兩個新 Context**。清單設計 context 只帶 views.py + 報告主題 1;搬移 context 只帶對照表(**不帶** views.py 全文——Codex 自己會開檔案)。
- **Review**:搬移執行前**快速檢查對照表**(函式名是否真實存在,抽查 5 個);完工後不需高階 review(純搬移),由測試把關。

---

### Part 5 — AI session 狀態單一化

- **目標**:報告主題 2——以 `AIConversation` turns 為權威,`session_state` 只留 metadata,cache 改 read-through,消除三份 source of truth。
- **任務**:
  1. **設計**(高階模型):決定 `session_state` 保留欄位(phase/stance/drift)、history 重建時機與快取失效規則、`_rebuild_session_state_from_turns` 的去留、對 reply / WS stream / analyze / restore 四條路徑的影響面;輸出一頁設計文件。
  2. **實作**(中階模型):照設計改 `dialogue_session` service(P4 已抽好,所以改動集中一個模組)、調整 4 條路徑、migration(若 session_state 欄位縮減需要 data migration 則寫)。
  3. 測試:重連恢復、reply 後 history 正確、並發 reply+analyze 不互相覆蓋。
- **執行前讀取**:設計者:報告主題 2 + P2(AI session race)節、P4 產出的 `dialogue_session.py`、`_rebuild_session_state_from_turns` 現行程式碼、HANDOFF_P1 的鎖方案(analyze 已加鎖,設計需相容);實作者:設計文件 + HANDOFF_P4。
- **預期輸出**:改版 service、migration(如需)、測試、設計文件存 `backend/docs/refactor/p5_session_design.md`、HANDOFF_P5。
- **驗收標準**:全測試綠;kill cache 後 session 可完整從 DB 恢復;reply 與 analyze 並發不遺失 history(測試證明);`session_state` 中不再存 history 全文。
- **App/模型**:設計:Claude App / **Fable 5**(備用 Opus 4.8 → GPT-5.6);實作:Claude App / Sonnet 5(備用 Codex / GPT-5.5)。
- **選擇原因**:這是全案第二個「錯了很難察覺」的架構決策(資料一致性),值得第二次動用 Fable;實作因 P4 已把改動面集中,中階可勝任。
- **用量等級**:設計=高(量小)、實作=中。
- **交接內容**:設計文件、保留/移除欄位清單、快取失效規則、四條路徑的行為變化。
- **風險**:與 P1 的鎖方案衝突 → 設計 context 必帶 P1 方案文件;舊資料的 session_state 含 history → migration 策略寫明(建議:不清舊資料,讀取時忽略舊 history 欄位)。
- **Context**:**兩個新 Context**(設計/實作)。設計帶:上列 4 項,合計應 <2000 行。實作帶:設計文件 + dialogue_session.py。**不帶**:P2/P3/P4 細節、code review 報告其他章節。
- **Review**:設計文件完成後,**由實作 Agent 之外的角色(你本人)過目一次再放行**——這是全案唯一建議人工把關的決策點;實作 Agent 開工只讀設計文件。

---

### Part 6 — 清理包(重複碼 / topic 102 / 死碼 / 文件)

- **目標**:報告主題 3、4、7、8 的低風險清理。
- **任務**(每項獨立 commit):
  1. `core/env.py` 收斂 `_env_bool`(3 處)與共用常數(匿名名稱、SESSION_TTL、cache key)。
  2. `FIXED_ANCHORS` / root name fallback 移入 TOPIC_CONFIGS,查無 config 即 raise。
  3. 刪 `main.py`、處置 `Issue`(預設:刪除,除非 grep frontend 有用)、移除未用 import、修正 `DialoguePhase.from_turn_count` 註解。
  4. 文件:README 記 sqlite+pgvector 限制、settings/CLAUDE.md Django 版本對齊。
- **執行前讀取**:報告主題 3/4/7/8;HANDOFF_P4 的對照表(常數搬移後位置可能已變);不需其他 Part 內容。
- **預期輸出**:4 個 commit、HANDOFF_P6。
- **驗收標準**:全測試綠;`grep -rn "def _env_bool" backend` 只剩 1 處;不帶 anchors 的新 topic config 會明確報錯(有測試)。
- **App/模型**:Codex App / GPT-5.4(備用:Haiku 4.5)。
- **用量等級**:低。
- **交接內容**:刪除清單(供 P7 確認無誤刪)、topic config 新的必填欄位。
- **風險**:topic config 改為 raise 可能讓現有測試用的假 topic 炸掉 → 測試 fixture 補上必填欄位。
- **Context**:**新 Context**。**不帶**任何前面 Part 的實作細節,只帶 HANDOFF_P4 對照表 + 報告 4 節。
- **Review**:只確認交接文件;無需 review 程式碼。

---

### Part 7 — 最終整合 Review 與結案

- **目標**:高階模型對 P1~P6 的 diff 做一次整合 review,跑全回歸,產出結案報告。
- **任務**:
  1. 以 `git diff <P0基線commit>...HEAD` 為範圍做 code review,重點:P1 鎖語意、P5 資料一致性、P2 async/sync 邊界、各 Part 之間的接縫(P4 搬移後 P5 改動是否一致)。
  2. 全測試 + 手動冒煙(如環境允許):建 session→reply→analyze→history 列表。
  3. 修 review 發現的問題(小問題當場修;大問題開 NEEDS_DECISION 清單)。
  4. 產出結案報告(對照原始 code review 報告逐項標 已修/未修/延後)。
- **執行前讀取**:原始 code review 報告(逐項核銷用)、HANDOFF_P1~P6 全部(這是唯一需要讀全部交接文件的 Part)、diff。**仍不需要**讀歷史對話。
- **預期輸出**:結案報告 `backend/docs/refactor/CLOSEOUT.md`、修正 commit、HANDOFF_P7(給未來維護者)。
- **驗收標準**:review 發現的 blocker 為 0;全測試綠;結案報告逐項核銷完成。
- **App/模型**:Claude App / **Fable 5** 或 Opus 4.8(備用:GPT-5.6)。diff review 是高階模型的核心價值場景。
- **用量等級**:高。
- **風險**:diff 太大超出單一 context → 按 Part 分段 review(P1+P5 一段——並發與一致性;P2+P3 一段;P4+P6 一段——機械類快速掃過),分段結論彙整進結案報告。
- **Context**:**新 Context**(必要時分 2~3 個,如上)。帶:交接文件全集 + 分段 diff。**不帶**:任何中間對話、被否決的方案細節(交接文件裡的決策紀錄已足夠)。
- **Review**:本 Part 就是 Review 本身;P4/P6(純機械)只需快速掃過,P1/P5 必須逐行。

---

## 3. Context Window 策略總表

| Part | Context | 帶入 | 明確不帶 |
|---|---|---|---|
| P0 | 新 | 本 Part 章節 | 一切歷史 |
| P1 | 新×2(設計/實作) | 報告 2 節+140 行碼 / 設計方案+目標檔 | views.py、其他報告章節 |
| P2 | 新 | HANDOFF_P0 測試指令+4 檔案指定段 | semantic_tree 內容、HANDOFF_P1(可並行,不依賴) |
| P3 | 新(4 項共用) | 報告 4 節+指定程式碼段 | P1/P2 實作細節 |
| P4 | 新×2(清單/搬移) | views.py 全檔(僅清單設計) / 對照表 | 報告其他章節 |
| P5 | 新×2(設計/實作) | 報告 2 節+P1 方案+service 檔 / 設計文件 | P2/P3/P4 細節 |
| P6 | 新 | 報告 4 節+P4 對照表 | 其他一切 |
| P7 | 新(可分段) | 全部 HANDOFF+分段 diff | 中間對話、被否決方案 |

**通用原則**:
- 沿用舊 Context 只發生在「同一 Part 內」;跨 Part 一律新開。理由:每個 Part 的檔案交集刻意設計為近零,舊 context 只會干擾。
- 「帶入程式碼」以**指定行段**為準,不貼全檔(App 內 Agent 可自行開檔,prompt 只給路徑+行號)。
- 交接靠 HANDOFF 文件,不靠對話記憶;HANDOFF 控制在 1~2 頁。

## 4. Review 策略總表

| Part | 開工前 Review | 程度 | 完工後高階 Review |
|---|---|---|---|
| P0 | 無 | — | 否 |
| P1 | 實作前確認設計方案 | 快速(10 min) | **是(P7 逐行)** |
| P2 | 讀 HANDOFF_P0(測試指令) | 只看交接文件 | P7 中等 |
| P3 | 讀 HANDOFF_P2 測試指令 | 只看交接文件 | P7 快掃 |
| P4 | 抽查對照表 5 個函式 | 快速 | 否(測試把關) |
| P5 | 設計文件由**人工**放行 | 完整(僅設計文件) | **是(P7 逐行)** |
| P6 | 讀 HANDOFF_P4 對照表 | 只看交接文件 | P7 快掃 |
| P7 | 讀全部 HANDOFF | 本身即 review | — |

## 5. 使用量控制與降級

**必用高階(Fable/Opus/GPT-5.6)**:P1 設計、P5 設計、P7 review。其餘一律中低階。

**降級鏈**:
- Fable 5 → Opus 4.8 → GPT-5.6(跨 App 交接:把該任務的小 context 打包成單一 prompt)
- Sonnet 5 ↔ GPT-5.5(互為備援)
- Haiku 4.5 ↔ GPT-5.4(互為備援)
- Claude App 整體額度盡 → 當日剩餘 Part 全走 Codex;反之亦然。P1/P5 設計若兩邊高階都不可用 → **暫停該 Part**,先做 P3/P6 等低階 Part(它們無依賴)。

**依賴關係允許的重排**:P0 → {P1 ∥ P2} → P3 → P4 → P5 → P6 → P7。
- P1(只動 semantic_tree.py)與 P2(動 consumers/views/新 service)檔案無交集,可並行。
- **P3 必須排在 P2 之後**:兩者都會改 `consumers.py` 與 `views.py`,並行會 merge conflict。
- 額度緊時優先序:P1 > P2 > P3 > P4 > P5 > P6(P7 永遠最後)。

**壓縮交接**:HANDOFF 不貼程式碼,只寫「檔案:行為變化」;測試輸出只留摘要;決策只寫「選了什麼+為什麼+否決了什麼」三行式。

## 6. 異常與中斷處理

| 情況 | 處理 |
|---|---|
| Part 中途達使用上限 | 立即停實作 → 用剩餘額度寫 HANDOFF(標記「未完成邊界」:哪些檔改到一半)→ commit WIP 到 branch → 下一個 Agent 從 HANDOFF 續作 |
| Context 快滿 | 先 commit 已完成部分 → 寫 HANDOFF → 同 Part 開新 context,只帶 HANDOFF |
| 前手沒寫 HANDOFF | 新 Agent **不要**通靈:用 `git log --oneline` + `git diff` 該 Part branch 重建狀態,重建結果先寫成 HANDOFF 再開工(用低階模型做這件事) |
| 模型間架構決策衝突 | 以「設計文件」為準(P1/P5 有正式設計文件);無設計文件的衝突 → 標 NEEDS_DECISION 交人類裁決,不投票、不自行折衷 |
| 實作偏離計畫 | 小偏離:交接文件記錄原因即可;動到介面/資料格式:停下,標 NEEDS_DECISION |
| 測試失敗且額度將盡 | 不硬修:記錄失敗輸出 + 懷疑點進 HANDOFF,revert 到最後綠的 commit,把紅的改動放 WIP branch |
| 新 Agent 看不懂前手輸出 | 先讀 p{n} 設計文件與 move map;仍不懂 → 花一次低階模型呼叫請前手模型(同型號新 context)解釋交接文件,禁止直接重讀全 repo |
| 需要回退 | 每 Part 獨立 branch + P0 基線 tag(`refactor-baseline`);回退 = 棄該 branch,不影響其他 Part |

## 7. 標準交接文件模板

```markdown
# HANDOFF_P{n} — {Part 名稱}
- 專案:Take A Bridge backend refactor
- Part:{n} / {名稱};Branch:{branch};日期:{date}
- 使用模型與 App:{model} @ {app}

## 本 Part 目標
{一句話}

## 已完成
- {項目}(commit: {hash})

## 修改/新增檔案
- {path}:{一句話行為變化}

## 影響模組與擁有者(給隊友判斷是否要跟進)
- {模組代號 m3/m4/…}:{改了什麼} → 需通知 {owner,如 陳彩希(前端)/葉錦諦(資料)};無則寫「無跨模組影響」

## 重要決策(選了什麼 / 為什麼 / 否決了什麼)
- {三行式}

## 測試
- 指令:{cmd}
- 結果:{通過 N / 失敗 M;失敗摘要或「全綠」}

## 未完成 / 已知問題
- {項目}(標記 NEEDS_DECISION 者需人類裁決)

## 風險與限制
- {項目}

## 給下一個 Agent
- 必讀:{檔案/章節清單,盡量 ≤3 項}
- 不需重看:{明確排除項}
- 下一步任務:{具體動詞開頭}
- 驗收條件:{可機械檢查的條件}
- 建議 Context:{新開;帶入 X、Y;不帶 Z}
- 是否需要 Review 本 Part:{否 / 快速 / P7 逐行}
- 啟動 Prompt:{可直接貼用的一段}
```

## 8. 各 Part 啟動 Prompt 產生規則

每個 HANDOFF 的「啟動 Prompt」按此骨架填:

```
你是 Take A Bridge backend refactor 的 Part {n} 執行者。
開啟 repo {path},切到 branch refactor/m{模組}-p{n}-{slug}(從當前 feat/Light 切出;a/b 拆分的 Part 共用同一分支)。
先讀:(1) backend/docs/refactor/handoffs/HANDOFF_P{n-1}.md
     (2) backend/docs/refactor/EXECUTION_PLAN.md 的「Part {n}」章節
不要讀其他歷史文件或全 repo 掃描。
你的任務:{Part 任務 1..k}
驗收:{驗收標準}
完成後:跑 {測試指令},把摘要寫進 HANDOFF_P{n}(模板見執行計畫第 7 節),commit 並停止。
額度不足或遇到未涵蓋的架構決策時:停止實作,優先寫 HANDOFF。
```
