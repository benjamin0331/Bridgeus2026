# CONTEXT.md — BridgeUs 開發狀態

> 最後更新：2026-06-29（M6 Part F：平台體驗回饋問卷 — PlatformFeedback model/API + 前端單頁問卷，接於 debriefing 之後）

> 2026-06-29（M6 Part F 平台體驗回饋）：
> - 後端 `api/models.py` 新增 `PlatformFeedback`（OneToOne→`PostDialogueResponse`）：F1–F5 滿意度 Likert（`ux_matching`/`ux_chatroom`/`ux_nlp_intervention`/`ux_ccnd`/`ux_overall`）、F6 `nps_score`(0–10, CheckConstraint)、F7 `ux_improvement`(nullable)。helper：`mean_ux()`、`nps_category()`。migration `0010_platformfeedback`。
> - `POST /api/platform-feedback/`（`PlatformFeedbackView`，IsAuthenticated）：以 `response_id` 驗證歸屬，`update_or_create` 冪等寫入。serializer：`PlatformFeedbackSerializer` / `PlatformFeedbackOutputSerializer`。
> - 前端 `pages/PlatformFeedbackPage.jsx`(+`.css`)：單頁，沿用 post-questionnaire 的 `pq-` 風格 + NPS 0–10 自訂列。`DebriefingPage` 確認同意/撤回後一律導向 Part F（帶 `responseId`、`withdrawn`），Part F 送出後顯示感謝頁（撤回者顯示撤回版文案）。route `/platform-feedback` 已加入 `App.jsx`。
> - 測試 `api/tests.py::PlatformFeedbackApiTests`（6 項，全綠）：auth、metrics 計算、選填空白、重送更新、NPS 越界、他人 response 拒絕。

## 專案目標

BridgeUs（橋得攏）— AI 驅動的去極化對話平台
核心功能：異質觀點配對、AI Agent 對話（RAG + LLM）、CCND 概念認知網路圖視覺化

---

## 技術棧

| 層級 | 技術 |
|------|------|
| 後端 | Django 6 + Django REST Framework + Django Channels（ASGI/WebSocket） |
| 前端 | React + Vite |
| 資料庫 | SQLite（dev）/ PostgreSQL（prod）、Django Cache（LocMem dev / Redis prod） |
| NLP | Sentence-Transformers（本地 embedding）、LangChain + ChromaDB（RAG） |
| LLM | Claude Sonnet（Anthropic SDK，主要）/ Gemini / OpenAI（備選，透過 llm_provider 抽象） |
| Package 管理 | uv（Python 3.13.5） |
| ASGI 伺服器 | uvicorn（WebSocket 支援） |

**環境變數（.env）：**
- `LLM_PROVIDER`：`claude` / `gemini` / `openai`
- `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY`
- `DB_ENGINE`：`sqlite`（預設）/ `postgres`
- `USE_REDIS_CACHE`：`false`（預設）/ `true`
- `USE_REDIS_CHANNEL`：`false`（預設）/ `true`（H-H 多 worker 時必須開）
- `REDIS_URL`：`USE_REDIS_CACHE` 或 `USE_REDIS_CHANNEL` 為 true 時必填
- `CLAUDE_CHAT_MODEL`：預設 `claude-sonnet-4-6`

**ChromaDB 知識庫：**
- Collection `nuclear_energy_all`：1449 chunks（新聞 184 篇 + 法律 230 條 + PTT 40 篇）

**H-H NLP 模型：**
- Embedding：`paraphrase-multilingual-MiniLM-L12-v2`（384 維，支援中文）
  - 注意：HH_Architecture.md 原寫 768 維為誤，MiniLM 實際輸出 384 維，已修正 VectorField
- 情緒偵測：`lxyuan/distilbert-base-multilingual-cased-sentiments-student`（~500MB）
  - 使用 negative-class probability 作為情緒強度分數（0–1）
  - 已知限制：模型為 topic-sensitive，含「事故」等負面詞的事實陳述分數偏高
  - 閾值 `EMOTION_THRESHOLD = 0.7`，待真實數據調整
- 攻擊性過濾：關鍵字黑名單（35 詞，frozenset O(1) 查找）
  - Stage 2 分類器候選：`thu-coai/roberta-base-cold`，暫不引入（VPS 記憶體考量）

---

## 當前進度

### M1 — 使用者認證

**已完成：**
- JWT 認證（djangorestframework-simplejwt）
- 登入 / 刷新 token endpoint
- 前端 LoginPage.jsx

---

### M2 — 議題選擇 & 立場測量

**已完成：**
- `api/dialogue_topics.py`：`TOPIC_CONFIGS`，含李克特量表問題與開放式問題配置
- 立場分數計算：加權平均、反向題處理、support / neutral / oppose 分類
- `DialogueSurveyView`（GET `/api/dialogue/topics/<id>/survey/`）
- `SurveyModal.jsx`：前端問卷元件

---

### M3 — 異質配對 & AI Agent

**已完成：**

**異質配對（matcher.py）：**
- `UserStanceProfile` / `DialogueMatch` / `MatchQueueEntry` models（migration 0002 已完成）
- `enqueue_for_matching()`：建立立場 profile、加入配對佇列
- `get_matching_state()`：查詢配對狀態
- `cancel_matching()`：取消配對
- REST API：MatchingJoinView / MatchingStatusView / MatchingCancelView

**AI Agent（ai_agent.py）：**
- `DialogueAgent`：RAG 檢索（ChromaDB + LangChain）+ Claude 生成 pipeline
- `DialogueSession`：多輪對話狀態（歷史、立場、階段），`to_dict()` / `from_dict()` 序列化
- `DialoguePhase`：三階段策略（engagement / confrontation / convergence）
- `astream_respond()`：async 生成器，逐字 streaming 給 WebSocket
- `split_into_chunks()`：依中文句末標點分割，每段最多 30 字
- Prompt caching：Anthropic ephemeral cache
- `system_prompt_v2.txt`：格式約束（每輪最多 3 句、單論點）

**Dialogue Session REST API（views.py）：**
- `POST /api/dialogue/sessions/`：建立 session，存入 Django cache
- `POST /api/dialogue/sessions/<id>/reply/`：同步回覆（REST 備用介面，仍保留）

---

### M4 — 即時對話室（WebSocket）

**已完成（H-AI）：**
- `api/consumers.py`：`DialogueStreamConsumer`（AsyncWebsocketConsumer）
  - JWT auth from `?token=` query param
  - 接收 `{"type": "user_message", "content": "..."}`
  - 呼叫 `agent.astream_respond()` 逐字推送 `{"type": "agent_stream", "content": "..."}`
  - 完成後送 `{"type": "agent_stream_end"}`
  - 錯誤時送 `{"type": "error", "content": "..."}`
- `api/routing.py`：WebSocket URL `ws/dialogue/<session_id>/`
- 前端 `TopicChat.jsx`：WebSocket 逐字串流顯示、單氣泡 append 模式

**已完成（H-H Phase 1）：**
- `chat/models.py`：`Conversation`、`Message` 兩張資料表
  - `Conversation`：topic_id、user_a/b（FK）、session_number（1/2）、status、started_at/ended_at
  - `Message`：conversation（FK）、sender（FK）、content、timestamp、emotion_score（nullable）、dialogue_phase（nullable）、embedding（VectorField 384 維，nullable）
  - migrations：`0001_initial`、`0002_alter_message_embedding_dim`
- `chat/consumers.py`：`HumanHumanConsumer`
  - URL param `user_id`（integer，暫無 JWT）
  - connect 驗證：User 存在、Conversation 存在、user 是參與者（否則 4003）
  - relay 優先：group_send 不等 DB，訊息廣播給雙方
  - 每則訊息 async 存入 Message 表（DB 失敗只 log，不中斷 relay）
  - disconnect 自動離開 group
- `chat/routing.py`：`ws/hh/<conversation_id>/`（integer ID）
- `BridgeUs_Django/asgi.py`：合併 `ai_ws_patterns + hh_ws_patterns`
- `settings.py`：`USE_REDIS_CHANNEL` 環境變數，true 時切換至 `channels_redis`

**已完成（H-H Phase 2 — NLP Pipeline）：**
- `chat/services/embedding.py`：Embedding service
  - Singleton 載入 `paraphrase-multilingual-MiniLM-L12-v2`（double-checked locking）
  - `get_embedding(text)` → `list[float]`（384 維）
  - `get_embeddings(texts, batch_size=32)` → `list[list[float]]`
  - `cosine_similarity(vec_a, vec_b)` → `float`
  - `cosine_distance(vec_a, vec_b)` → `float`
  - `aget_embedding` / `aget_embeddings`：`sync_to_async`，`thread_sensitive=False`
- `chat/services/emotion.py`：情緒強度偵測
  - Singleton 載入 `lxyuan/distilbert-base-multilingual-cased-sentiments-student`
  - `analyze_emotion(text)` → `{"score": float, "label": str, "is_over_threshold": bool}`
  - score = negative-class probability（0–1）；閾值 0.7
  - `aget_analyze_emotion`：async wrapper
- `chat/services/filter.py`：攻擊性詞彙過濾（兩階段）
  - `check_content_sync(text)` → Stage 1 黑名單，同步，< 50ms，consumer 攔截專用
  - `check_content(text)` → Stage 1 + Stage 2 TODO stub
  - `acheck_content`：async wrapper
  - `chat/services/_blacklist.py`：35 詞 frozenset（人身攻擊 21、威脅 4、歧視 4、粗口 5）
  - Stage 2 候選：`thu-coai/roberta-base-cold`，暫不引入

**已完成（H-H Phase 3 — Consumer NLP 整合）：**
- `chat/consumers.py`：`HumanHumanConsumer` 整合 NLP pipeline
  - 模組頂層 import `aget_embedding` / `aget_analyze_emotion`（避免 coroutine 首次 import 阻塞 event loop）
  - `check_content_sync()` 同步攔截：命中黑名單 → 推送 `{"type":"system_prompt","category":"content_blocked"}` 給發言者，中止轉發
  - `CONTENT_BLOCKED_MESSAGES`（3 條）/ `EMOTION_WARNING_MESSAGES`（4 條）隨機選取
  - `group_send()` 即時轉發，不等後續分析
  - `asyncio.create_task(_run_nlp_analysis(message_id, content))`：fire-and-forget
    - `aget_embedding()` → `Message.embedding` DB update
    - `aget_analyze_emotion()` → `Message.emotion_score` DB update
    - 若 `is_over_threshold` → 推送 `{"type":"system_prompt","category":"emotion_warning"}` 給發言者
    - 所有步驟各自 try/except，失敗只 log，不中斷轉發
- `chat/tests_consumer.py`：4 項 NLP 整合測試
  - **注意：Phase 8 大幅改寫 consumer receive() 流程後，此項整合邏輯已調整（見 Phase 8）**

**已完成（H-H Phase 4 — 離題偵測）：**
- `chat/services/topic.py`：離題偵測 service
  - `get_topic_anchor_embedding(topic_description)` → `list[float]`（384 維）
  - `check_topic_relevance(conversation_id, user_id, anchor_embedding, window=5)` → `{"relevance_score": float, "is_off_topic": bool}`
    - **Phase 8 改為 window-based**：從 DB 取該用戶最近 window 則有 embedding 的訊息，平均後與錨點比較
    - 無訊息（含 embedding）時 fail-open（視為在議題內）；只算發言者本人訊息
  - `TOPIC_RELEVANCE_THRESHOLD = 0.25`（預定 0.3，實測「日本福島事件讓很多人改變想法」得 0.2938 故調降）
  - async wrapper：`aget_topic_anchor_embedding` / `acheck_topic_relevance`
- `chat/models.py`：`Conversation` 新增 `topic_anchor_embedding`（VectorField 384 維，nullable）
- `chat/migrations/0003_conversation_topic_anchor_embedding.py`
- `chat/consumers.py`：`connect()` 快取 `self.topic_anchor`；`_run_nlp_analysis()` 呼叫 `acheck_topic_relevance(conv_id, user_id, anchor)`
  - 離題推送 `{"type":"system_prompt","category":"off_topic"}` 給發言者；anchor=None 時略過
  - `OFF_TOPIC_MESSAGES`（3 條）
- `chat/tests_topic.py`：**Phase 8 完整改寫**，共 15 項測試

**已完成（H-H Phase 5 — 僵局偵測 + 關鍵詞抽取）：**
- `chat/services/stalemate.py`
  - `detect_stalemate(conversation_id, window_size=5)` → `{"is_stalemate": bool, "distance_trend": list[float], "std_dev": float}`
    - 各 user 最近 window_size 則 embedding 訊息按 recency 成對，算 cosine_distance
    - `std_dev < STALEMATE_THRESHOLD=0.05` AND `n_pairs >= window_size` → 僵局
    - 實測：穩定立場（unit_vec noise σ=0.002）std_dev=0.004；漸移立場 std_dev=0.707
  - `extract_opponent_keywords(conversation_id, user_id, n_messages=5, top_n=2)` → `list[str]`
    - 對方最近 n_messages 則發言 → jieba 分詞 → KeyBERT 抽候選詞（MMR diversity=0.6）
    - 按 cosine_distance 到「當前用戶最近發言」排序降冪，返回 top_n（最少被回應的論點）
    - 實測：核能議題文本抽出如「能力」「台灣」「能源」等詞；函式回傳 list[str]，len ≤ top_n
  - `build_stalemate_prompt(keywords)` → str（4 個模板隨機選取）
  - async wrapper：`adetect_stalemate` / `aextract_opponent_keywords`
- 安裝：`keybert==0.9.0`、`jieba==0.42.1`（已加入 pyproject.toml 依賴）
- `chat/tests_stalemate.py`：16 項測試

**已完成（H-H Phase 6 — 立場漂移追蹤 + 累積觸發整合）：**

**新增 Models（migration 0004）：**
- `Conversation.user_a_initial_embedding` / `user_b_initial_embedding`（VectorField 384 維，nullable）
  - 問卷開放式回答 embedding，配對時填入；現在先加欄位，填值邏輯後面做
- `StanceDrift`：conversation (FK)、user (FK)、drift_value (FloatField)、measured_at (auto)

**`chat/services/drift.py`：**
- `calculate_drift(conversation_id, user_id)` → `{"drift_value": float, "direction": str}`
  - `drift_value = cosine_distance(mean_interval_embedding, initial_embedding)`
  - direction：diff > DIRECTION_THRESHOLD=0.02 → "approaching"；< -0.02 → "diverging"；else "stable"
  - 第一次測量無比較基準 → "stable"；initial_embedding=None 或無訊息 → 提前返回 `{0.0, "stable"}`
  - 儲存 StanceDrift 記錄
  - 實測：round1（近 initial）drift=0.0015；round2（遠 initial）drift=1.0018，direction="approaching"
- async wrapper：`acalculate_drift`

**`chat/consumers.py` 累積觸發邏輯：**
- `connect()` 初始化 `self.char_count = 0`、`self.last_analysis_time`
- `receive()` 每則訊息後：`char_count += len(content)`；若 `≥ 200 chars` 或 `≥ 900 秒` → 重置計數器 + `create_task(_run_periodic_analysis())`
- `_run_periodic_analysis()`：
  1. `acalculate_drift()` → 存入 StanceDrift（初始 embedding 為 None 時 fail-open）
  2. `adetect_stalemate()` → 若僵局：`aextract_opponent_keywords()` + `build_stalemate_prompt()` → 推送 `{"type":"system_prompt","category":"stalemate_hint"}`

**`chat/tests_drift.py`：12 項測試**（含 200-char consumer trigger 整合測試）

---

**已完成（H-H Phase 7 — 對話結束處理）：**

**新增 Models（migration 0005）：**
- `Conversation.summary`（TextField nullable）：LLM 生成的對話摘要
- `Conversation.stats`（JSONField nullable）：session 統計數據快照

**`chat/services/session.py`：**
- `end_session(conversation_id, *, blocked_count=0, system_prompts_triggered=0)` → `dict`
  - 標記 `Conversation.status = COMPLETED`，設 `ended_at = now()`
  - 收集統計：`duration_minutes`、`total_messages`、`messages_per_user`、`avg_emotion_score_per_user`、`blocked_count`、`system_prompts_triggered`、`stance_drift_final`（各 user 最後一筆 StanceDrift）
  - 儲存至 `Conversation.stats`
- `generate_summary(conversation_id)` → `str`
  - 撈全部 Messages 組成 transcript，呼叫 Claude API（`claude-sonnet-4-6`）
  - `ANTHROPIC_API_KEY` 未設定 → 返回 `""`（記 warning log，不拋例外）
  - API 失敗 → 返回 `""`（記 error log）
  - 儲存至 `Conversation.summary`
  - 注意：這是 H-H 模組唯一的 LLM 呼叫
- async wrappers：`aend_session` / `agenerate_summary`

**`chat/consumers.py` 對話結束邏輯：**
- `connect()` 新增：`self._session_ended = False`、`self.blocked_count`、`self.system_prompts_triggered`
  - 啟動 background task：`_session_timer()`
  - **注意：Phase 8 移除 `_silence_watcher()`，只保留 explicit + 60 min timer 兩種結束條件**
- `receive()` 新增：
  - `{"type":"end_session"}` → `_end_conversation("explicit")`
  - `blocked_count` / `system_prompts_triggered` 計數器累加
- `disconnect()` 取消 timer task
- `_session_timer()`：`sleep(3600)` → `_end_conversation("timeout")`
- `_end_conversation(reason)`：
  1. 冪等保護（`_session_ended`）
  2. 取消 background tasks
  3. `aend_session()` 收集 stats
  4. `channel_layer.group_send("session.ended", ...)` 廣播雙方
  5. `create_task(_generate_summary_bg())`（非阻塞）
- `session_ended(event)` channel handler：send `{"type":"session_ended","reason":...,"stats":...}` → `close()`
  - 注意：close 在 handler 內（不在 `_end_conversation`），讓 event loop 先 dispatch channel layer message 再關閉 WebSocket

**`chat/tests_session.py`：19 項測試**
- `end_session`：status、ended_at、duration、messages、emotion scores、blocked passthrough、drift、DB save、empty conv
- `generate_summary`：no API key、no messages、mock LLM 成功、API error fallback
- Consumer：explicit end trigger、stats 欄位完整性

---

**已完成（H-H Phase 8 — AI 輔助介入）：**

**新增 Model（migration 0006）：**
- `AISuggestion`：記錄每次 AI 介入的完整資料
  - `conversation` (FK)、`user` (FK)、`category`（rephrase / direction / redirect）
  - `original_content`（使用者原文）、`suggested_content`（AI 改寫版）
  - `user_action`（accept / modify / ignore，nullable＝尚未回應）
  - `modified_content`（使用者修改版，僅 modify 時填入）
  - `created_at`（auto）

**`chat/services/ai_assist.py`（新模組）：**
- `rephrase_message(original_text, topic)` → `str`：呼叫 Claude API，將情緒化發言重述為理性語氣，保留立場
- `suggest_direction(conversation_id, topic)` → `str`：取最近 6 則對話，要求 Claude 提供新討論角度（已接入 consumer `_inactivity_watcher`）
- `redirect_to_topic(conversation_id, topic)` → `str`：取最近 6 則對話，要求 Claude 溫和引導回主題（已接入 consumer 離題偵測，取代固定模板）
- 全部均有 fallback 到預寫中文模板（API 未設定或呼叫失敗）
- async wrappers：`arephrase_message` / `asuggest_direction` / `aredirect_to_topic`

**`chat/consumers.py` 重大改寫：**

`receive()` 新流程（情緒**攔截**，不再只是警告）：
1. `end_session` / suggestion response → 分流處理
2. Stage 1（同步）：`check_content_sync()` → 攔截黑名單
3. Stage 2（**await**）：`aget_analyze_emotion()` → **情緒超標則攔截，不轉發給對方**
4. 正常路徑：`_relay_and_persist(content, emotion_score)`

`_handle_emotion_overflow(content)`（情緒超標時觸發）：
- `await arephrase_message()` → Claude 改寫（或 fallback 模板）
- `AISuggestion.acreate()` 存入 DB（`user_action=None`）
- 儲存 `self._pending_suggestion = {"category":"rephrase", "original_content": ..., "suggestion_id": ...}`
- 只傳給發言者：`{"type":"ai_suggestion","category":"rephrase","original_content":...,"suggested_content":...,"actions":["accept","modify","ignore"]}`
- **Bob 這端什麼都收不到**

`_handle_suggestion_response(data)` （使用者回應時觸發，Phase 9 擴充）：
- 讀取 `pending["category"]`（預設 `"rephrase"`）決定是否 relay
- `rephrase`：accept / modify / ignore → 轉發對應內容給對方 + DB update
- `redirect` / `direction`：accept / ignore → **只更新 DB，不 relay 任何內容**

`_relay_and_persist(content, emotion_score=None)`（正常路徑 + suggestion 回應共用）：
- 更新 `_conversation_last_active[conv_id]`（Phase 9 新增，供 inactivity watcher 讀取）
- `group_send` → 廣播雙方
- `Message.acreate()` 存入 DB
- `create_task(_run_nlp_analysis)` → embedding + topic check（背景）
- 字數累積觸發 `_run_periodic_analysis` → drift + stalemate（背景）

**已移除（Phase 8）：**
- `_silence_watcher()` 及所有沉默偵測邏輯（`SILENCE_WARNING_MESSAGE`、`_SILENCE_WARN_SECONDS`、`_SILENCE_END_SECONDS`）
- consumer 結束條件：原 3 種（explicit / timer / silence）→ 現 2 種（explicit / 60 min timer）

---

**已完成（H-H Phase 9 — redirect_to_topic + inactivity_watcher 接入 consumer）：**

**`chat/consumers.py` 新增 / 修改：**

模組層級新增：
- `DIRECTION_FALLBACK_MESSAGES`（3 條冷場備用模板）
- `_INACTIVITY_SECONDS = 120`（2 分鐘）、`_INACTIVITY_CHECK_INTERVAL = 30`（秒）
- `_conversation_last_active: dict[int, datetime] = {}`：module-level 共享字典，單 process 架構下兩個 consumer 共用，記錄各 conversation 最後活躍時間

`connect()` 新增：
- 初始化 `_conversation_last_active[conv_id]`
- 啟動 `_inactivity_task`（**僅 user_a** 的 consumer 執行，防止雙重觸發）

`disconnect()` / `_end_conversation()` 新增：
- 取消 `_inactivity_task`

**`_handle_off_topic(message_id)`（新方法，取代 off_topic 固定模板）：**
- `await aredirect_to_topic()` → LLM 生成引導提示（失敗 fallback `OFF_TOPIC_MESSAGES`）
- `AISuggestion.acreate()` 存入 DB（`original_content=None`、`category=REDIRECT`）
- 設定 `self._pending_suggestion = {"category":"redirect", "original_content":None, "suggestion_id":...}`
- 推送給**發言者**：`{"type":"ai_suggestion","category":"redirect","suggested_content":...,"actions":["accept","ignore"]}`
- accept / ignore 只更新 DB，不 relay（無內容可 relay）

**`_inactivity_watcher()`（新 background task，僅 user_a 跑）：**
- 每 30s 輪詢 `_conversation_last_active[conv_id]`
- elapsed ≥ 120s 且尚未觸發 → `triggered=True`，`create_task(_suggest_direction_bg())`
- 任一方發言後（`_conversation_last_active` 更新）→ `triggered=False` 重置，下次靜默可再觸發

**`_suggest_direction_bg()`（新方法）：**
- `await asuggest_direction()` → LLM 生成討論方向（失敗 fallback `DIRECTION_FALLBACK_MESSAGES`）
- `channel_layer.group_send("session.ai_suggestion", ...)` → 廣播雙方

**`session_ai_suggestion(event)`（新 channel layer handler）：**
- 每個 consumer 各自：`AISuggestion.acreate(category=DIRECTION, original_content=None)`
- 設定 `self._pending_suggestion = {"category":"direction", ...}`
- 傳送 WebSocket：`{"type":"ai_suggestion","category":"direction","suggested_content":...,"actions":["accept","ignore"]}`

**Consumer 目前所有 async task：**
| Task | 建立時機 | 說明 |
|------|---------|------|
| `_session_timer` | `connect()` | 60 min hard cap |
| `_inactivity_task` | `connect()`（僅 user_a） | 2 分鐘靜默觸發方向建議 |
| `_run_nlp_analysis` | `_relay_and_persist()` 每則 | embedding、emotion_score、離題偵測 |
| `_run_periodic_analysis` | `_relay_and_persist()` 累積觸發 | 漂移計算、僵局偵測 |
| `_suggest_direction_bg` | `_inactivity_watcher` 觸發時 | LLM → group_send direction suggestion |
| `_generate_summary_bg` | `_end_conversation()` | 對話摘要生成 |

**`chat/tests_ai_assist.py`（14 項測試，Phase 10 更新 2 項）：**
- `rephrase_message`：mock LLM 成功、**no API key 現在預期 RuntimeError（已更新）**、**API error 現在預期 RuntimeError（已更新）**、輸出情緒分數低於原文
- `suggest_direction` / `redirect_to_topic`：mock LLM、no API key fallback
- Consumer emotion overflow：高情緒訊息不轉發給 Bob、建立 AISuggestion 記錄（actions 含 modify/ignore）
- Consumer suggestion response：accept / modify / ignore 正確轉發 + DB 更新
- Consumer 正常訊息：低情緒直接轉發，無 AISuggestion

**`chat/tests_topic.py`：完整改寫（15 項測試），配合 window-based API**
- 新增：no messages fail-open、no embedding fail-open、window 行為、只算發言者訊息、async wrapper 一致性
- 移除：舊 per-message API 測試

**`chat/tests_consumer.py`：Phase 9+10 新增，現共 23 項**
- 原有 3 項：正常轉發、NLP DB 寫入、黑名單攔截
- redirect 系列（5 項）：LLM 路徑、LLM 失敗 fallback、AISuggestion 記錄、accept/ignore 不 relay
- direction 系列（7 項）：雙方收到、只觸發一次、活動後重置計時器、LLM 失敗 fallback、DB 兩筆記錄、accept/ignore 不 relay
- **Phase 10 新增 8 項：**
  - inactivity max 3 triggers、grace period 抑制觸發、min interval 防止快速再觸發、disconnect 退出 watcher
  - rephrase LLM 失敗 → 只有 modify/ignore actions
  - modify 二次情緒檢查再攔截、達到最大攔截次數強制 relay
  - AISuggestion 研究欄位驗證（trigger_score、response_time_ms、final_content、context_message_ids）

---

**已完成（H-H Phase 10 — Edge Cases 修補）：**

**`chat/services/ai_assist.py` 修改：**
- `rephrase_message()` 改為在 LLM 不可用時 **raise RuntimeError**（不再回傳 fallback 字串）
- 移除 `_REPHRASE_FALLBACKS` list（改由 consumer 控制 fallback）

**`chat/models.py` + `migration 0007`：**
- `AISuggestion` 新增 4 個研究欄位（均 nullable）：
  - `response_time_ms`（IntegerField）：建議推送到用戶回應的時間差（毫秒）
  - `trigger_score`（FloatField）：觸發時的具體分數（情緒 or 離題餘弦距離）
  - `final_content`（TextField）：最終實際送出給對方的內容
  - `context_message_ids`（JSONField）：觸發時前 5 則訊息的 ID list

**`chat/consumers.py` 全面重寫（Phase 10 修補）：**

模組層級新增常數：
- `_REPHRASE_FALLBACK_TEMPLATE = "你的發言可能帶有較強烈的情緒，建議修改後再發送。"`
- `_REPHRASE_MAX_INTERCEPTS = 3`
- `_INACTIVITY_MAX_TRIGGERS = 3`、`_INACTIVITY_MIN_INTERVAL_SECONDS = 300`、`_INACTIVITY_GRACE_SECONDS = 180`
- `_conversation_connected_at: dict[int, datetime]`、`_conversation_disconnected: set[int]`

`connect()` 新增：
- `self._rephrase_retry_count = 0`
- `_conversation_connected_at[conv_id] = now()`

`disconnect()` 新增：
- `_conversation_disconnected.add(conv_id)`

`receive()` 新增：
- 每則新 content 訊息重置 `self._rephrase_retry_count = 0`
- 傳遞 `trigger_score=emotion["score"]` 給 `_handle_emotion_overflow()`

`_handle_emotion_overflow(content, trigger_score=None)`（重大修改）：
- 每次攔截 `self._rephrase_retry_count += 1`
- LLM 成功 → `actions=["accept","modify","ignore"]`；失敗 catch → `suggested=_REPHRASE_FALLBACK_TEMPLATE`，`actions=["modify","ignore"]`
- 儲存 `trigger_score`、`context_message_ids`（前 5 則訊息 ID）、`sent_at` 至 pending

`_handle_suggestion_response(data)`（修改）：
- 計算 `response_time_ms` 從 `pending["sent_at"]`
- **rephrase + modify** 路徑：DB 更新後呼叫 `_handle_modify_emotion_check(content, suggestion_id)`，不直接 relay
- accept / ignore：計算 `final_content` 後更新 DB，然後 relay

`_handle_modify_emotion_check(content, prev_suggestion_id)`（**新方法**）：
- 重新執行 `aget_analyze_emotion(content)`
- 若仍超標且 `retry_count < MAX` → 再次呼叫 `_handle_emotion_overflow(content)`（循環攔截）
- 否則（低情緒 or 達到最大次數）→ 更新 prev AISuggestion `final_content=content`，force relay

`_inactivity_watcher()`（重大重寫，加入 4 個限制）：
1. **Max 3 triggers**：`trigger_count >= 3` → `return`
2. **Grace period 180s**：連線後 3 分鐘內不觸發（`_conversation_connected_at` 比較）
3. **Min interval 300s**：上次觸發後 5 分鐘內不重複觸發
4. **Disconnect exit**：`conv_id in _conversation_disconnected` → `return`

`_handle_off_topic(message_id, trigger_score=None)` / `session_ai_suggestion(event)`（修改）：
- 儲存 `trigger_score`、`context_message_ids`、`sent_at` 至 AISuggestion + pending

`_get_recent_message_ids(n=5)`（**新 helper**）：
- async 取前 n 則訊息 ID（降序後 reverse），回傳 `list[int]`

---

### M5 / M6 — 尚未開始

- M5：CCND 概念認知網路圖（D3.js force-directed graph）
- M6：對話摘要、立場偏移報告、知識庫沉澱

---

## 測試覆蓋

| 檔案 | 測試數 | 說明 |
|------|--------|------|
| `chat/tests.py` | 5 | WebSocket relay、DB 持久化、拒絕非參與者 |
| `chat/tests_embedding.py` | 11 | 維度驗證、中文語意相似度、async wrapper |
| `chat/tests_emotion.py` | 10 | 平和 vs 攻擊語句分數、閾值一致性、async wrapper |
| `chat/tests_filter.py` | 21 | 黑名單命中（頭/中/尾）、強烈但合法語句不攔截、sync/async 一致 |
| `chat/tests_consumer.py` | 23 | NLP 整合（3）+ redirect LLM（5）+ direction inactivity（7）+ Phase 10 edge cases（8） |
| `chat/tests_topic.py` | 15 | Window-based 離題偵測：no-msg fail-open、on/off-topic、window 行為、async |
| `chat/tests_stalemate.py` | 16 | 僵局偵測（穩定/漸移）、關鍵詞抽取、提示模板 |
| `chat/tests_drift.py` | 12 | 漂移計算、direction 邏輯、DB 記錄、200-char consumer 觸發 |
| `chat/tests_session.py` | 19 | end_session 統計、generate_summary mock LLM、consumer 結束觸發 |
| `chat/tests_ai_assist.py` | 14 | rephrase mock/fallback/calmer、overflow 攔截不轉發、accept/modify/ignore |
| **合計** | **146** | 全部通過（2026-05-15 驗證） |

執行：
```bash
cd backend
uv run pytest chat/tests.py chat/tests_embedding.py chat/tests_emotion.py chat/tests_filter.py chat/tests_consumer.py chat/tests_topic.py chat/tests_stalemate.py chat/tests_drift.py chat/tests_session.py chat/tests_ai_assist.py -v
# 或精簡執行 consumer 相關：
uv run pytest chat/tests_consumer.py chat/tests_ai_assist.py -v
```

---

## 檔案結構

```
P_BridgeUS/
├── CLAUDE.md
├── CONTEXT.md
├── README.md
├── .env / .env.example
├── docker-compose.yml
├── docs/
│   ├── BridgeUs_API_Spec.md
│   └── HH_Architecture.md             ← H-H 系統架構文件
├── data/
│   └── nuclear_energy/                ← RAG 原始語料（不 commit）
├── backend/
│   ├── manage.py
│   ├── main.py                        ← uvicorn 啟動入口
│   ├── pyproject.toml                 ← uv 依賴管理
│   ├── chroma_data/                   ← ChromaDB 持久化（不 commit）
│   ├── BridgeUs_Django/
│   │   ├── settings.py                ← DB / Cache / Channels / JWT / Redis 設定
│   │   ├── urls.py
│   │   ├── asgi.py                    ← ProtocolTypeRouter（合併 ai + hh WebSocket）
│   │   └── wsgi.py
│   ├── api/                           ← H-AI 對話 app
│   │   ├── models.py                  ← AIConversation, UserStanceProfile,
│   │   │                                 DialogueMatch, MatchQueueEntry
│   │   ├── views.py
│   │   ├── serializers.py
│   │   ├── urls.py
│   │   ├── consumers.py               ← DialogueStreamConsumer（H-AI WebSocket）
│   │   ├── routing.py                 ← ws/dialogue/<session_id>/
│   │   ├── dialogue_topics.py
│   │   └── migrations/
│   │       ├── 0001_initial.py
│   │       └── 0002_dialoguematch_...py
│   ├── chat/                          ← H-H 對話 app
│   │   ├── models.py                  ← Conversation, Message（VectorField 384 維）
│   │   ├── consumers.py               ← HumanHumanConsumer（H-H WebSocket）
│   │   ├── routing.py                 ← ws/hh/<conversation_id>/
│   │   ├── tests.py                   ← WebSocket relay 測試（5）
│   │   ├── tests_embedding.py         ← Embedding service 測試（11）
│   │   ├── tests_emotion.py           ← 情緒偵測測試（10）
│   │   ├── tests_filter.py            ← 攻擊性過濾測試（21）
│   │   ├── tests_consumer.py          ← Consumer NLP 整合測試（3）
│   │   ├── tests_topic.py             ← 離題偵測測試（15，window-based）
│   │   ├── tests_session.py           ← 對話結束測試（19）
│   │   ├── tests_ai_assist.py         ← AI 輔助介入測試（14）
│   │   ├── services/
│   │   │   ├── _blacklist.py          ← 攻擊性詞彙黑名單（35 詞，frozenset）
│   │   │   ├── embedding.py           ← get_embedding / cosine_similarity 等
│   │   │   ├── emotion.py             ← analyze_emotion / EMOTION_THRESHOLD
│   │   │   ├── filter.py              ← check_content_sync / check_content / acheck_content
│   │   │   ├── topic.py               ← check_topic_relevance(conv_id, user_id, anchor, window=5)
│   │   │   ├── stalemate.py           ← detect_stalemate / extract_opponent_keywords / build_stalemate_prompt
│   │   │   ├── drift.py               ← calculate_drift / DIRECTION_THRESHOLD=0.02
│   │   │   ├── session.py             ← end_session / generate_summary
│   │   │   └── ai_assist.py           ← rephrase_message / suggest_direction / redirect_to_topic（Claude API + fallback）
│   │   └── migrations/
│   │       ├── 0001_initial.py
│   │       ├── 0002_alter_message_embedding_dim.py
│   │       ├── 0003_conversation_topic_anchor_embedding.py
│   │       ├── 0004_drift_model_and_initial_embeddings.py
│   │       ├── 0005_conversation_summary_stats.py
│   │       └── 0006_aisuggestion.py
│   ├── apps/
│   │   └── matching/
│   │       └── services/
│   │           ├── ai_agent.py
│   │           └── matcher.py
│   ├── core/
│   │   ├── llm_provider.py            ← LLM / embedding provider（H-AI 用）
│   │   └── chroma_utils.py
│   └── scripts/
│       └── build_knowledge_base.py
└── frontend/
    ├── package.json
    └── src/
        ├── App.jsx
        ├── api/client.js
        ├── components/
        │   ├── Navbar.jsx
        │   ├── Sidebar.jsx
        │   ├── IssueCard.jsx
        │   ├── ActionCard.jsx
        │   └── SurveyModal.jsx
        └── pages/
            ├── LoginPage.jsx
            ├── HomePage.jsx
            ├── TopicChat.jsx          ← H-AI 對話（WebSocket 串流）
            └── KnowledgeBase.jsx
```

---

## 啟動方式（開發）

```bash
# 後端
cd backend
uvicorn BridgeUs_Django.asgi:application --host 0.0.0.0 --port 8000 --reload

# 前端
cd frontend
npm run dev   # Vite dev server，port 5173
```

---

## 待辦事項

### 近期（H-H NLP Pipeline）

- [x] `chat/services/embedding.py`：Embedding service（384 維）
- [x] `chat/services/emotion.py`：情緒強度偵測（lxyuan distilbert）
- [x] `chat/services/filter.py`：攻擊性詞彙過濾（Stage 1 黑名單）
- [x] Consumer 整合 NLP pipeline（攻擊性過濾同步攔截、情緒偵測非同步 fire-and-forget）
- [x] `chat/services/topic.py`：離題偵測（embedding vs 議題錨點，threshold=0.25）
- [x] `chat/services/stalemate.py`：僵局偵測 + 關鍵詞抽取 + 提示模板
- [x] `chat/services/drift.py`：立場漂移追蹤（每 200 字 / 15 分鐘觸發）
- [x] `chat/services/session.py`：對話結束處理（統計收集 + LLM 摘要）
- [x] Consumer 兩種結束條件：explicit message、60 分鐘 timer（沉默偵測已移除）
- [x] `chat/services/ai_assist.py`：AI 輔助介入（rephrase / suggest_direction / redirect_to_topic）
- [x] `AISuggestion` model：記錄 AI 介入歷程（migration 0006）
- [x] Consumer receive() 改寫：情緒超標攔截 + ai_suggestion + accept/modify/ignore 回應流程
- [x] `chat/services/topic.py` 改寫：window-based API（DB 查詢最近 N 則訊息）
- [x] `redirect_to_topic` 接入 consumer 離題偵測（LLM 生成 + fallback，推送 ai_suggestion/redirect）
- [x] `suggest_direction` 接入 consumer（`_inactivity_watcher`：雙方靜默 2 分鐘觸發，推送 ai_suggestion/direction）
- [ ] 問卷初始立場 embedding 填入（M2/M3 整合）
- [ ] CCND WebSocket 推送（前端介面待確認）
- [ ] API Spec 更新（`docs/BridgeUs_API_Spec.md`）

### 近期（基礎設施）

- [ ] Docker Compose 完整配置（含 PostgreSQL + Redis）
- [ ] PostgreSQL 切換（`CREATE EXTENSION IF NOT EXISTS vector`）
- [ ] 多議題知識庫（目前只有核能）

### 後期

- [ ] M5：CCND 概念認知網路圖（pgvector 查詢 + D3.js force-directed graph）
- [ ] M6：對話摘要生成（LLM，session 結束後）
- [ ] M6：立場偏移量化報告
- [ ] CI/CD（GitHub Actions）
