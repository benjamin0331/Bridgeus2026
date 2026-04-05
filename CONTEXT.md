# CONTEXT.md — BridgeUs 開發狀態

## 專案目標

BridgeUs（橋得攏）— AI 驅動的去極化對話平台
核心功能：異質觀點配對、AI Agent 對話（RAG + LLM）、CCND 概念認知網路圖視覺化

---

## 技術棧

| 層級 | 技術 |
|------|------|
| 後端 | Django + Django Channels（WebSocket） |
| 前端 | React + D3.js |
| 資料庫 | PostgreSQL + pgvector（用戶/對話）、ChromaDB（RAG 知識庫） |
| NLP | Sentence-Transformers（本地 embedding）、LangChain |
| LLM | Gemini（開發測試）/ Claude 3.5 Sonnet（production） |

**環境設定：**
- `LLM_PROVIDER=gemini`：chat 用 Gemini API
- `EMBEDDING_PROVIDER=local`：embedding 用本地 sentence-transformers（all-MiniLM-L6-v2，384 維）
- ChromaDB collection `nuclear_energy_all`：1449 chunks，已建立完成

---

## 當前進度

### 開發優先順序
> **目前專注：AI Agent 功能完整化 → 之後才進入其他模組（M1/M2/M4/M5/M6）**

### M3 AI Agent — 已完成部分

- `DialogueAgent`：RAG 檢索 + LLM 生成的完整 pipeline，可正常運行
- `DialogueSession`：多輪對話狀態管理（歷史紀錄、立場資訊、對話階段）
  - `to_dict()` / `from_dict()`：序列化介面已實作，供 M4 斷線重連後還原 session
- `DialoguePhase`：三階段策略（engagement / confrontation / convergence）
- System prompt v2：角色設定、對話策略、格式規範、安全護欄
- LLM provider 抽象層：Gemini / Claude / OpenAI 可切換，embedding 獨立控制
  - `_cached_llm` / `_cached_embeddings`：以 `lru_cache` 快取，避免每次重建實例
- 知識庫 ingest 工具：`scripts/build_knowledge_base.py`
- 核能議題知識庫：`nuclear_energy_all`（新聞 184 篇 + 法律 230 條 + PTT 40 篇）

**2026-04-05 code review 修正：**
- 修正 prompt 重複傳入使用者訊息的 bug（`conversation_history` + `human` message 雙重傳入）
- `build_knowledge_base.py` 的 Chroma import 統一為 `langchain_chroma`（原為 `langchain_community`）
- LLM / embeddings 加 `lru_cache`，HuggingFaceEmbeddings 不再每次重載模型權重
- `format_history()` 加 `max_turns` 參數（預設 20 輪），防止長對話超過 context window
- `respond()` 加 `try/except`，LLM API 失敗時拋 `RuntimeError` 附輪次資訊，不直接炸 WebSocket
- `DialogueSession` / `DialogueMessage` 加 `to_dict()` / `from_dict()` 序列化介面

### M3 AI Agent — 尚未完成

| 項目 | 說明 |
|------|------|
| 多議題動態 collection | collection name 目前靠呼叫端傳入，需建立議題 slug → collection name 的映射機制 |
| Session 持久化接 DB | `to_dict/from_dict` 已就緒，但尚未接 PostgreSQL / Django cache |
| API endpoint | `matching/views.py` 空的，外部（M4）無法透過 HTTP 呼叫 |
| M4 WebSocket 接入 | `respond()` 已就緒，但尚無 consumer 呼叫 |
| M2 立場向量傳入 | `agent_stance`、`user_stance_score` 目前為呼叫端 hardcoded 值，需接 M2 輸出 |
| M5 對話階段更新 | `dialogue_phase` 目前用輪次 fallback，尚未接 NLP 語義距離計算 |

### M3 異質配對演算法 — 尚未開始

CLAUDE.md 規格：基於立場向量的異質配對演算法（歐氏距離 / 餘弦相似度）

- `matching/models.py`、`views.py`、`serializers.py` 目前全為空
- 建議待 M2 的 stance vector 格式確定後再實作，避免重工

> 議題偏離偵測、情緒強度閾值冷靜提示屬於 **M4** 的責任，不在 M3 範圍內。

### 其他模組（待 AI Agent 完成後再進行）

- M1 JWT 認證：scaffold 建立，邏輯未實作
- M2 量表問卷與立場向量化：scaffold 建立，邏輯未實作
- M4 WebSocket 對話室：scaffold 建立，邏輯未實作
- M5 CCND / NLP 分析：scaffold 建立，邏輯未實作
- M6 摘要與知識庫：scaffold 建立，邏輯未實作
- 前端（React + D3.js）：尚未開始
- PostgreSQL schema / migration：尚未建立
- Docker Compose 環境：設定檔存在，尚未完整配置

---

## 檔案結構

```
P_BridgeUS/
├── CLAUDE.md                          ← 專案規範與架構說明
├── CONTEXT.md                         ← 本檔案，開發狀態追蹤
├── README.md
├── .env                               ← LLM_PROVIDER, GOOGLE_API_KEY 等
├── .env.example
├── docker-compose.yml
├── docs/
│   └── BridgeUs_API_Spec.md
├── data/
│   └── nuclear_energy/                ← RAG 原始語料（已 ingest，不 commit）
│       ├── articles.json              ← 新聞文章（184 篇）
│       ├── laws_processed.json        ← 法律條文（230 條）
│       └── ptt_processed.json         ← PTT 討論（40 篇）
├── scripts/
│   └── build_knowledge_base.py        ← RAG 知識庫 ingest 工具
└── backend/
    ├── manage.py
    ├── requirements.txt
    ├── chroma_data/                   ← ChromaDB 持久化（不 commit）
    ├── config/
    │   ├── settings/
    │   │   ├── base.py
    │   │   ├── dev.py
    │   │   └── prod.py
    │   ├── urls.py
    │   ├── asgi.py
    │   └── wsgi.py
    ├── core/
    │   ├── llm_provider.py            ← LLM/embedding provider 抽象層（含 lru_cache）
    │   └── models.py
    └── apps/
        ├── auth_module/               ← M1（scaffold only）
        ├── stance/                    ← M2（scaffold only）
        ├── matching/                  ← M3
        │   ├── prompts/
        │   │   └── system_prompt_v2.txt  ← AI Agent system prompt
        │   └── services/
        │       └── ai_agent.py           ← DialogueAgent 主體
        ├── dialogue/                  ← M4（scaffold only）
        ├── nlp_analysis/              ← M5（scaffold only）
        └── summary/                   ← M6（scaffold only）
```

---

## 待辦事項

### AI Agent（進行中）

- [ ] 多議題動態 collection 選擇（議題 slug → collection name 映射）
- [ ] Session 持久化（接 PostgreSQL / Django cache）
- [ ] REST API endpoint（`matching/views.py`）
- [ ] 對外介面測試與文件更新

### M3 異質配對演算法（待 M2 stance vector 格式確定後）

- [ ] Matching 演算法實作（歐氏距離 / 餘弦相似度）
- [ ] `matching/models.py` DB schema
- [ ] `matching/views.py` + `matching/serializers.py`

### 後續模組（AI Agent 完成後）

- [ ] M1 JWT 認證實作
- [ ] M2 量表問卷與立場向量化
- [ ] M4 WebSocket 對話室 + 接入 DialogueAgent
- [ ] M5 NLP 語義距離計算 + CCND 生成
- [ ] M6 對話摘要與立場偏移報告
- [ ] 前端 React + D3.js
- [ ] PostgreSQL schema / migration
- [ ] Docker Compose 完整配置
