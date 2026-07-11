# BridgeUs Backend Code Review — Refactor 準備報告

- **日期**:2026-07-11
- **範圍**:`backend/` 全部 Python 程式碼(約 11,500 行,不含 migrations / venv)
- **目的**:為後續 refactor 盤點問題,分為「正確性/效能問題」與「Refactor 主題」兩部分
- **審查基準**:feat/Light(與 main 無 diff,即整個 codebase 現況)

---

## 一、整體評價

服務層(`matcher.py`、`matching_algorithm.py`、`semantic_tree.py` 的純函式部分)品質不錯:有 docstring、有測試、錯誤類別定義清楚、matching 演算法被刻意隔離在單一檔案方便調參。

主要技術債集中在三處:

1. **`api` 變成 god app** — 所有 model、六個模組的 view 全塞在一個 app,與 CLAUDE.md 規劃的模組結構脫節。
2. **AI session 狀態有三份 source of truth** — cache、`DialogueSessionRecord.session_state`、`AIConversation` turns,靠 rebuild 函式對齊,並發時會互相覆蓋。
3. **多人同時使用時才會爆發的並發/效能問題** — 鎖內打外部 API、N+1、deadlock、無限流。單人開發測試感覺不到,實驗上線(30+30 人)時會直接踩到。

---

## 二、正確性/效能問題(依嚴重度排序)

### P1 — 鎖內呼叫 OpenAI,鎖可能被持有近 100 秒
**位置**:`apps/matching/services/semantic_tree.py:1292`(`analyze_pending_room_messages`)

在 `select_for_update` 鎖住 `DialogueMatch` row 的交易內,同步呼叫最多 5 次、每次 timeout 20 秒的 OpenAI API。

**影響**:分析期間,同一個 match 的所有操作全部卡在 row lock 上——presence 更新(`mark_match_participant_connected`)、關房(`close_match`)、REST/WS 發訊息前的 `_touch_room_match_for_user_activity`。OpenAI 一慢,整間聊天室停擺。

**修法方向**:鎖內只做「挑出 pending messages」和「寫回結果」兩段;LLM 呼叫移到鎖外。寫回前重新檢查 `analyzedSourceIds` 避免重複。

---

### P1 — History 列表 N+1,且全量載入所有訊息
**位置**:`api/views.py:1056`(`HistoryConversationListView`)

對每筆 `DialogueSessionRecord` 與每個 `DialogueMatch`,都把全部訊息載入記憶體,只為了算 `message_count` 和取最後一則 preview。

**影響**:使用者累積 200 場對話後,一次 GET 發出 200+ 次查詢並搬運所有對話全文;列表頁隨資料量線性變慢,在 2-4GB VPS 上很快成為最慢端點。

**修法方向**:改用 `annotate(Count(...))` + 子查詢取最後一則訊息;detail 才載入全文。

---

### P1 — REST 發訊息繞過整條安全管線
**位置**:`api/views.py:1419`(`MatchingRoomMessagesView.post`)

REST 版建立 `MatchMessage` 時不做黑名單過濾、不做情緒偵測、不算 embedding、也不 `group_send` 廣播;WS 版(`consumers.py`)全部都做。

**影響**:
- 使用者改用 REST POST 即可**繞過黑名單與情緒攔截**——對一個以「情緒攔截」為核心機制的去極化研究平台,這是可被直接繞過的洞。
- 訊息不會推播給對方的 WebSocket(對方看不到,除非重新拉取)。
- 沒有 embedding → 被排除在 stance drift / stalemate 分析之外,**實驗數據出現系統性缺漏**。

**修法方向**:抽一個共用的 `post_match_message()` service(過濾 → 情緒 → 建立 → 廣播 → 排程分析),REST 與 WS 都走它;或干脆讓 REST 版只讀不寫。

---

### P2 — 配對 enqueue 並發 deadlock
**位置**:`apps/matching/services/matcher.py:500` + `_find_best_candidate`

兩人同時 `enqueue_for_matching`:A 先 `select_for_update` 鎖自己的 queue entry,再掃描候選人時試圖鎖 B 的 entry;B 同時反向操作。加鎖順序相反 → Postgres 偵測 deadlock 後 abort 一方,該使用者收到未處理的 500,且沒有 retry。

**修法方向**:統一加鎖順序(例如永遠按 entry id 排序加鎖),或捕捉 `OperationalError` 後 retry 一次。

---

### P2 — AI session 語意樹分析無鎖 + 狀態 last-write-wins
**位置**:`apps/matching/services/semantic_tree.py:1353`(`analyze_pending_ai_conversations`)

match 版有 `select_for_update`,AI session 版完全沒有鎖;且 session 狀態是「cache/DB 讀出 → 記憶體修改 → 整包覆蓋寫回」。

**影響**:前端重試或連點分析按鈕造成並發請求時:
- 兩個請求讀到相同的 pending turns,各打一次 OpenAI(**雙倍花費**);
- `analysisHistory` / `analyzedSourceIds` 重複 append;
- 後寫的 `_persist_dialogue_session_record` 覆蓋先寫的——reply / WS stream / analyze 三條路徑並發時互相蓋狀態,對話歷史可能遺失。

**修法方向**:見「Refactor 主題 2」——狀態單一化後這個 race 自然消失;短期可先對 `DialogueSessionRecord` 加 `select_for_update`。

---

### P2 — Guest 登入與 LLM endpoint 完全沒有限流
**位置**:`api/views.py:1571`(`GuestLoginView`);全站 REST_FRAMEWORK 設定

- `POST /api/guest/` 無 throttle:任何人可無限建立**永久** User 帳號,每個附 7 天 refresh token → 灌爆 `auth_user` 表。
- 取得 token 後迴圈打 `/dialogue/sessions/<id>/reply/`,**每次都燒一次 Claude API**,LLM 帳單無上限。

**修法方向**:DRF `AnonRateThrottle` / `UserRateThrottle`(至少對 guest、reply、semantic-tree analyze 三類端點);guest 帳號考慮定期清理。

---

### P3 — WebSocket receive 假設 payload 形狀
**位置**:`api/consumers.py:111`、`consumers.py:339`

`json.loads` 結果若不是 dict(如 `[1]`、`"x"`、`123`),`data.get` 拋 `AttributeError`;`content` 若不是 str,`.strip()` 同樣炸。未處理例外 → 連線被斷。

**修法方向**:開頭加 `if not isinstance(data, dict): return`,`content` 用 `str(...)` 或 isinstance 檢查。

---

### P3 — `AIConversationSerializer` 用 `fields='__all__'`
**位置**:`api/serializers.py:9` + `AIConversationDetail`(RetrieveUpdate**Destroy**)

- GET 列表每筆回傳 384 個 float 的 embedding,payload 膨脹數十倍。
- PUT 可讓客戶端直接改寫 `ai_response`、`embedding`、`session_id`——**污染 stance-drift 研究數據**。

**修法方向**:白名單 fields,embedding 等分析欄位設 read-only 或不輸出;Detail view 考慮拿掉 Update/Destroy。

---

### P3 — DialogueAgent 同步初始化阻塞 event loop
**位置**:`api/consumers.py:142`(呼叫 `views.get_dialogue_agent`)

`get_dialogue_agent` 是 `lru_cache` 的同步函式,首次呼叫會載入 HuggingFace embedding 模型 + 初始化 Chroma collection(秒級),卻直接在 async `_stream_response` 內呼叫 → 阻塞整個 event loop,期間同 process 所有 WebSocket 凍結。`maxsize=8` 被逐出後會再度發生。

**修法方向**:包 `sync_to_async`,或在 `warm_nlp_models` management command 裡預熱常用 collection。

---

### P3 — `thread_sensitive=False` 包 ORM 函式,DB 連線洩漏
**位置**:`apps/matching/services/hh_analysis.py:239-244`、`hh_ai.py:119-121`

`sync_to_async(..., thread_sensitive=False)` 讓函式跑在 thread pool 的任意執行緒;Django 每個執行緒各開一條 DB 連線,且不受 request 生命週期管理、不會被關閉。

**影響**:每則 WS 訊息觸發的 topic check / drift / stalemate 在不同執行緒累積閒置 Postgres 連線,長時間運作後逼近 `max_connections`,新請求開始被拒。

**修法方向**:純推論函式(embedding/emotion)維持 `thread_sensitive=False` 是對的;**含 ORM 查詢的函式**改用 `database_sync_to_async`,或把 DB 存取部分拆出來。

---

### 其他小項(正確性相關)

| 位置 | 問題 |
|---|---|
| `consumers.py` 查詢字串帶 JWT | token 會進 access log / proxy log(WS 常見做法,但要知情) |
| `views.py:1075` | 排序 fallback 用 naive `timezone.datetime.min` 與 aware datetime 比較會 TypeError(目前欄位皆非空所以踩不到,屬地雷) |
| `matcher.py` `get_matching_state` | GET 會寫 DB(touch / close / presence),副作用藏在 GET 語意裡 |
| `ai_agent.py` `astream_respond` | 只把最新一則當 user message,歷史全塞 system prompt;且 `prompt_vars` 沒有 `user_message`,若 prompt 檔含該佔位符會殘留字面文字 |

---

## 三、Refactor 主題(建議動刀順序)

### 主題 1:模組邊界(最大的一刀)

現況與 CLAUDE.md 目標結構(`apps/{auth_module,stance,matching,dialogue,nlp_analysis,summary}`)差距很大:

- **所有 model 都在 `api`**(M2 的 StanceProfile、M3 的 Match/Queue、M4 的 Message、M5 的分析欄位),`apps/matching` 沒有 model,`chat` 只剩 services。
- **`api/views.py` 1630 行**,裡面約 40 個 `_helper` 做的其實是 service-layer 的工作(session cache/persist/restore、stance 計分、history 組裝、presence payload)。
- **依賴方向是反的**:`consumers.py` 從 `api.views` import helper;services 反向 import `api.models`;到處 lazy import 是循環依賴的症狀。

**建議步驟**:
1. 先把 views 的 helpers 抽成 service 模組(`dialogue_session` service:cache/persist/restore/payload 一組;`stance_scoring` service:計分/分類/開放題解析一組)。**不搬 model、不改 URL**,風險最低。
2. consumers 改 import service 而非 views。
3. model 搬遷(需要 migrations `SeparateDatabaseAndState`)放最後,等前兩步穩定。

### 主題 2:AI session 狀態單一化

同一份對話狀態存在三處:cache dict、`DialogueSessionRecord.session_state`(JSON)、`AIConversation` turns,靠 `_rebuild_session_state_from_turns` 對齊。

**建議**:以 DB turns 為權威(它本來就每輪都寫),`session_state` 只留 metadata(phase、stance、drift),history 一律從 turns 重建;cache 純作 read-through。上面 P2 的並發覆蓋問題會一併消失。

### 主題 3:把 topic 102(核能)的預設值從共用程式碼拔掉

`FIXED_ANCHORS`、`ANCHOR_DESCRIPTIONS`(semantic_tree.py 頂部)、root name fallback `"核電"`(views.py:535)都是核能專屬卻寫成全域 fallback。新增議題時若 config 缺欄位,會**默默長出核能的樹**而不是報錯。

**建議**:一律從 `TOPIC_CONFIGS` 取,查不到就 raise;`FIXED_ANCHORS` 移進 topic 102 的 config(目前兩處重複定義)。

### 主題 4:收斂重複程式碼

| 重複項 | 出現位置 |
|---|---|
| `_env_bool` | `settings.py` / `matching_algorithm.py` / `hh_ai.py`(×3) |
| `_stats_dict` | `matcher.py` / `semantic_tree.py`(×2) |
| `_session_cache_key` + `SESSION_TTL_SECONDS` | `views.py` / `consumers.py`(×2) |
| 匿名顯示名 | views 用「匿名對話者」,serializer 與 consumer 用「匿名使用者」(不一致) |
| 向量清理 | `matching_algorithm._clean_vector` ≈ `semantic._clean_embedding` |

**建議**:建 `core/env.py`(env 讀取)與共用常數模組。

### 主題 5:收斂 LLM / Embedding 技術棧

目前同時存在四套呼叫方式:

| 路徑 | 技術 | 模型 |
|---|---|---|
| REST reply(`DialogueAgent.respond`) | LangChain `ChatAnthropic` | `CLAUDE_CHAT_MODEL` |
| WS stream(`astream_respond`) | raw anthropic SDK | `CLAUDE_CHAT_MODEL`(參數/組裝方式不同) |
| H-H 助手(`hh_ai._call_claude`) | raw anthropic SDK,每次新建 client | 同上 |
| 語意樹(`analyze_with_openai`) | raw urllib 打 OpenAI | `OPENAI_MODEL` |

加上**兩個不同的 embedding 模型**:對話向量用 `paraphrase-multilingual-MiniLM-L12-v2`(chat/services/embedding.py),RAG 用 `all-MiniLM-L6-v2`(core/llm_provider.py)。在 2-4GB VPS 上等於常駐:兩份 sentence-transformers + emotion DistilBERT(~500MB)+ 核能 BERT 分類器。

**建議**:Anthropic 呼叫統一進 `core/llm_provider`;確認兩個 embedding 模型是否刻意為之(維度不同、用途不同可以接受,但要寫進文件),評估 RAG 是否能共用 L12 模型省下一份權重。

### 主題 6:`match.stats` JSON 無上限成長

semantic tree 完整樹 + `analysisHistory`(含每則 `sourceText`、`invalidItems` 全文)+ presence 全塞同一個 JSONField,每次 save(包括每次 presence 更新)整包重寫。長對話會讓最頻繁的小更新搬移一大坨 JSON。

**建議**:`analysisHistory` 拆表或設長度上限;presence 與 semantic_tree 分開欄位存,避免互相牽動。

### 主題 7:清死碼

- `backend/main.py` — hello world,刪。
- `Issue` model + `IssueListCreateView` — 遺留 demo:`stance`/`emotion` 欄位從未寫入、手刻 dict 序列化、直接 FK `User` 而非 `settings.AUTH_USER_MODEL`、無分頁。刪掉或補完。
- `views.py` 匯入的 `MatchMessageSerializer` 實際上 room messages 走的是 `MatchingRoomMessagesSerializer` 內嵌版,確認後移除。
- `DialoguePhase.from_turn_count` 註解寫「僅供開發測試、正式版由 M5 判定」,但它是 production 唯一的 phase 判定——註解與現實要對齊。

### 主題 8:其他小項

- settings 檔頭寫 Django 6.0.4、CLAUDE.md 寫 Django 5.x,文件對齊。
- 預設 `DB_ENGINE=sqlite` 但 model 用 pgvector `VectorField`:SQLite 能存(存成文字)但不能做向量查詢;目前所有相似度都在 Python 端算所以沒炸,**值得寫進 README** 避免之後有人在 sqlite 上用 pgvector 查詢。
- `api/tests.py` 1802 行單檔;測試檔命名兩套並存(`tests.py` vs `tests_*.py`)。拆 views 時順便拆測試。
- 全站無分頁(history 列表、conversations、issues)。

---

## 四、衛生面(好消息)

- ✅ 沒有誤 commit `db.sqlite3`、`chroma_data/`、model weights(`.safetensors`)、`.env`。
- ✅ settings 的 production 保護做得不錯:DEBUG=false 時強制 SECRET_KEY / JWT_SIGNING_KEY 長度、DB_ENGINE 白名單、Redis 開關對應檢查。
- ✅ matching 演算法隔離在單檔、版本字串(`matching_algorithm_version`)有寫進 match 紀錄,利於實驗回溯。

---

## 五、建議執行順序

| 順位 | 項目 | 理由 |
|---|---|---|
| 1 | P1 三項(鎖內 OpenAI、History N+1、REST 繞過管線) | 實驗上線前必修,否則多人同時使用會直接故障/數據缺漏 |
| 2 | 限流(guest + LLM endpoints) | 防帳單與濫用,改動小 |
| 3 | 主題 1 第 1 步(views helpers 抽 service) | 不動 model、不改 API,為後續所有 refactor 鋪路 |
| 4 | 主題 2(session 狀態單一化) | 一併解掉 P2 的並發覆蓋 |
| 5 | P2/P3 其餘項 + 主題 3~7 | 可拆成多個小 PR 分工 |
