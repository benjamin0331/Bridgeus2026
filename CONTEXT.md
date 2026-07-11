# CONTEXT.md — BridgeUs 開發狀態

> 最後更新：2026-07-11（M3 CCND：核能議題本地 ML 分類器移植回主線）

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

**CCND 節點生成（semantic_tree.py）— 依議題分流 LLM / 本地 ML：**
- `analyze_text_for_tree()`：依 `topic_id` 分流的 dispatcher。
  - `topic_id=102`（核能）→ `nuclear_node_classifier.py`：本地兩階段 BERT 分類器
    （`hfl/chinese-roberta-wwm-ext` 微調），macro 模型判斷 anchor、micro 模型判斷
    pointName，不呼叫任何 LLM API。架構/訓練來源見
    `apps/matching/ml_models/nuclear_node_model/README.md`（訓練者：黃筱筑）。
  - 其他議題（如 103）→ 維持 `analyze_with_openai()` 生成式路徑。
  - **⚠️ 這條本地分類器路徑目前沒有權重檔**（`model.safetensors` x7，共
    ~2.7GB，依規定不進版控，見 `.gitignore`）。需從共用空間另外複製進
    `apps/matching/ml_models/nuclear_node_model/{model_macro,model_micro_0..5}/`
    才能真的跑起來；沒放的話 `classify()` 會直接丟 `FileNotFoundError`
    （刻意設計成明確報錯，不會靜默切回 OpenAI）。
  - 這份實作原本只存在 `feat/hsiao` 分支、沒有合併也沒有寫進任何狀態文件，
    差點連同分支一起遺失——2026-07-11 已補移植進主線並在此記錄，**之後只
    要動到 CCND 生成邏輯，這裡的說明要一起更新**，不要讓它再一次只活在某個
    人的本地分支裡。

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

**尚未完成：**
- Consumer 整合 NLP pipeline（攻擊性過濾同步攔截、情緒偵測非同步）
- 離題偵測（embedding vs 議題錨點向量）
- 準即時分析：立場漂移追蹤（每 200 字 / 15 分鐘）
- 僵局偵測（連續 N 輪語義距離無變化）
- CCND WebSocket 推送

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
| **合計** | **47** | |

執行：
```bash
cd backend
uv run pytest chat/tests.py chat/tests_embedding.py chat/tests_emotion.py chat/tests_filter.py -v
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
│   │   ├── services/
│   │   │   ├── _blacklist.py          ← 攻擊性詞彙黑名單（35 詞，frozenset）
│   │   │   ├── embedding.py           ← get_embedding / cosine_similarity 等
│   │   │   ├── emotion.py             ← analyze_emotion / EMOTION_THRESHOLD
│   │   │   └── filter.py              ← check_content_sync / check_content / acheck_content
│   │   └── migrations/
│   │       ├── 0001_initial.py
│   │       └── 0002_alter_message_embedding_dim.py
│   ├── apps/
│   │   └── matching/
│   │       ├── services/
│   │       │   ├── ai_agent.py
│   │       │   ├── matcher.py
│   │       │   ├── semantic_tree.py       ← CCND 節點生成，依 topic_id 分流
│   │       │   └── nuclear_node_classifier.py  ← topic 102 本地 BERT 分類器
│   │       └── ml_models/
│   │           └── nuclear_node_model/     ← 權重不進版控，見 README.md
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
- [ ] `chat/services/topic_deviation.py`：離題偵測（embedding vs 議題錨點）
- [ ] Consumer 整合 NLP pipeline（攻擊性過濾同步攔截、情緒偵測非同步）
- [ ] 準即時分析：立場漂移追蹤（每 200 字 / 15 分鐘觸發）
- [ ] 僵局偵測（連續 N 輪語義距離無變化）
- [ ] API Spec 更新（`docs/BridgeUs_API_Spec.md`）

### 近期（基礎設施）

- [ ] Docker Compose 完整配置（含 PostgreSQL + Redis）
- [ ] PostgreSQL 切換（`CREATE EXTENSION IF NOT EXISTS vector`）
- [ ] 多議題知識庫（目前只有核能）
- [ ] 補齊核能節點分類器權重檔（`apps/matching/ml_models/nuclear_node_model/`，見 M3 說明）

### 後期

- [ ] M5：CCND 概念認知網路圖（pgvector 查詢 + D3.js force-directed graph）
- [ ] M6：對話摘要生成（LLM，session 結束後）
- [ ] M6：立場偏移量化報告
- [ ] CI/CD（GitHub Actions）
