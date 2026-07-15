# CLAUDE.md — BridgeUs (橋得攏) Project Context

## 每次對話開始時的必要步驟

**每次新對話開始時，必須先閱讀 `CONTEXT.md`（位於專案根目錄 `h:\P_BridgeUS\CONTEXT.md`）以確認上次的進度、當前任務狀態與待辦事項，再開始任何工作。**

---

## Project Overview

BridgeUs (橋得攏) is an **heterogeneous-viewpoint dialogue platform** designed to combat echo chambers and social polarization. It matches users with opposing stances on public issues, facilitating structured depolarization dialogue through both human-human and human-AI agent interactions.

核心假設：與具備高品質知識架構及無情緒干擾的 AI 對話，能達到與真人異質對話同等甚至更穩定的去極化效果。

This is an **NSTC (國科會) undergraduate research grant project** (115 年度大專學生研究計畫), research period: 2026/07 – 2027/02 (8 months). Advisor: 張昭憲 (淡江大學資管系副教授).

---

## System Architecture — 6 Modules

```
┌─────────────────────────────────────────────────────┐
│                    Frontend (Web)                   │
│         React / D3.js (CCND force-directed graph)   │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP / WebSocket
              ┌────────▼────────┐
              │   API Gateway    │
              │  (Django REST)   │
              └────────┬────────┘
                       │
    ┌──────────────────┼──────────────────────┐
    │                  │                      │
┌───▼───┐  ┌──────────▼──────────┐  ┌────────▼────────┐
│ M1    │  │ M2                  │  │ M3              │
│ Auth  │  │ Topic Selection &   │  │ Heterogeneous   │
│ Module│  │ Stance Measurement  │  │ Matching &      │
│       │  │                     │  │ AI Agent Gen    │
└───────┘  └─────────────────────┘  └────────┬────────┘
                                             │
                                    ┌────────▼────────┐
                                    │ M4              │
                                    │ Real-time       │
                                    │ Dialogue Room   │
                                    │ (WebSocket)     │
                                    └────────┬────────┘
                                             │
                              ┌──────────────┼──────────────┐
                              │                             │
                     ┌────────▼────────┐          ┌────────▼────────┐
                     │ M5              │          │ M6              │
                     │ NLP Analysis &  │          │ Post-Dialogue   │
                     │ CCND Generation │          │ Summary &       │
                     │                 │          │ Knowledge Base  │
                     └─────────────────┘          └─────────────────┘
```

### Module Details

| Module | Name | 說明 |
|--------|------|------|
| **M1** | User Auth | JWT 認證、註冊/登入、帳號管理。表面匿名、實質具名機制。 |
| **M2** | Topic Selection & Stance Measurement | 議題選擇、李克特量表 + 開放式問卷、Sentence-Transformers 向量化立場、stance score 計算。 |
| **M3** | Heterogeneous Matching & AI Agent Generation | 基於立場向量的異質配對演算法（歐氏距離/餘弦相似度）、RAG-powered AI agent 生成（對立立場代理人）。 |
| **M4** | Real-time Dialogue Room | Django Channels WebSocket 即時對話、回應長度限制、議題偏離偵測、情緒強度閾值觸發冷靜提示。準即時分析：每 90 min 或累積 200 字觸發。 |
| **M5** | NLP Analysis & CCND Generation | 語義距離計算（Sentence-Transformers）、概念認知網路圖（Conceptual Cognitive Network Diagram）、D3.js force-directed graph 即時渲染、WebSocket 推送更新（延遲約 2-3 秒）。 |
| **M6** | Post-Dialogue Summary & Knowledge Base | 對話摘要生成、立場偏移量化報告、情緒分數變化、觀點知識庫沉澱。 |

---

## Tech Stack

### Backend
- **Language**: Python 3.11+
- **Framework**: Django 5.x + Django REST Framework
- **WebSocket**: Django Channels (ASGI, with Redis as channel layer)
- **Auth**: JWT (djangorestframework-simplejwt)

### Database
- **Primary DB**: PostgreSQL 16+
  - 用戶帳戶、對話紀錄、問卷數據、實驗相關資料
  - **pgvector** extension：儲存對話語義向量（每則發言的 embedding），支援向量相似度查詢，用於 CCND 節點距離計算
- **Vector Store (RAG)**: ChromaDB
  - 僅用於 RAG 知識庫：政府資料、新聞、學術報告等背景知識的嵌入向量
  - Collection 命名規範：`{topic_slug}_{source_type}`（如 `nuclear_energy_gov`, `military_service_news`）

### AI / NLP
- **LLM API**: Anthropic Claude 3.5 Sonnet (首選) / OpenAI GPT-4o (備選) / Google Gemini 1.5 Flash (大量實驗用)
- **Embedding**: Sentence-Transformers（語義向量轉換、立場距離計算）
- **NLP Preprocessing**: NLTK（分詞、停用詞移除、文字正規化）
- **RAG Framework**: LangChain（連結 ChromaDB 知識庫）

### Frontend
- **Framework**: 待定（React 為主要候選）
- **CCND Visualization**: D3.js force-directed graph
- **Real-time**: WebSocket client

### Infrastructure
- **Cloud**: VPS (2-4GB RAM)
- **Containerization**: Docker (recommended)
- **CI/CD**: GitHub Actions (planned)

---

## Team & Responsibilities

| Member | Role | 負責模組 |
|--------|------|---------|
| 賴則名 (Benjamin) | Project Manager | 系統架構、進度管理、AI agent、RAG後端與數據支援、報告整理 |
| 伍晨安 | Backend Developer | 模型訓練、API 開發、演算法（M3 matching + AI agent + RAG）|
| 陳彩希 | Frontend Developer | UI/UX、前端介面、D3.js CCND 視覺化 |
| 黃筱筑 | QA & Optimization | 測試、優化、前後端支援 |
| 葉錦諦 | Data Engineering | 資料收集、資料庫管理、資料分析、爬蟲 |


---

## Git Conventions

### Branch Strategy (GitHub Flow variant)

```
main              ← 穩定版本，永遠可部署，僅透過 PR 合併
├── dev           ← 整合分支，各功能完成後先合併到這裡做整合測試
│   ├── feat/m1-jwt-auth          ← 新功能
│   ├── feat/m3-matching-algo
│   ├── feat/m5-ccnd-websocket
│   ├── fix/m4-ws-disconnect      ← Bug 修復
│   ├── refactor/m2-questionnaire ← 重構
│   ├── docs/api-spec-update      ← 文件更新
│   └── test/m6-summary-unit      ← 測試
```

**規則：**
- `main`：只接受從 `dev` 來的 PR，需至少 1 人 review
- `dev`：各功能分支的整合點，合併前需通過 CI
- 功能分支命名：`{type}/m{module_number}-{brief-description}`
- 分支從 `dev` 切出，完成後 PR 回 `dev`
- 禁止直接 push 到 `main` 或 `dev`

### Commit Convention (Conventional Commits)

```
<type>(scope): <subject>

[optional body]

[optional footer]
```

**Types:**
| Type | 用途 | 範例 |
|------|------|------|
| `feat` | 新功能 | `feat(m3): implement cosine similarity matching` |
| `fix` | Bug 修復 | `fix(m4): resolve WebSocket reconnection loop` |
| `docs` | 文件 | `docs(api): update M2 stance endpoint schema` |
| `refactor` | 重構（不改功能） | `refactor(m1): extract JWT logic to middleware` |
| `test` | 測試 | `test(m5): add unit tests for embedding distance` |
| `chore` | 雜務（CI、依賴等） | `chore: update requirements.txt` |
| `style` | 格式（不影響邏輯） | `style(m2): fix PEP8 indentation` |

**Scope = 模組代號**：`m1` ~ `m6`，跨模組用 `core` 或省略

**規則：**
- Subject 用英文、小寫開頭、不加句號、不超過 72 字元
- Body 可中英混合，解釋 why not what
- Breaking change 加 `BREAKING CHANGE:` footer

### Pull Request Rules

**PR Title**: 同 commit convention 格式，如 `feat(m3): implement stance vector matching`

**PR Template:**
```markdown
## What
簡述這個 PR 做了什麼

## Why
為什麼需要這個改動

## Module
- [ ] M1 Auth
- [ ] M2 Topic & Stance
- [ ] M3 Matching & AI Agent
- [ ] M4 Dialogue Room
- [ ] M5 NLP & CCND
- [ ] M6 Summary & KB

## Checklist
- [ ] 本地測試通過
- [ ] 無 hardcoded secrets
- [ ] API 變更已更新 API Spec
- [ ] 有需要的話已加註解
```

**Merge 規則：**
- PR → `dev`：至少 1 位 reviewer approve
- PR → `main`：至少 2 位 reviewer approve（含 Benjamin）
- Squash merge 到 `dev`，merge commit 到 `main`
- CI 必須全綠才能合併

### .gitignore Essentials

```
# Python
__pycache__/
*.pyc
*.pyo
venv/
.env

# Django
db.sqlite3
media/
staticfiles/

# IDE
.vscode/
.idea/

# OS
.DS_Store
Thumbs.db

# Secrets
*.env
.env.*
!.env.example

# ChromaDB local data
chroma_data/

# Node (if frontend)
node_modules/
dist/
```

---

## Development Guidelines

### API Contract
- 所有模組間溝通透過已定義的 API Spec（`docs/BridgeUs_API_Spec.md`）
- 尚未完成的依賴模組使用 mock response 解耦
- API 變更需先更新 spec，再實作

### Environment Setup
```bash
# Backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env      # 填入 API keys & DB credentials

# Database
# PostgreSQL with pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

# ChromaDB (RAG knowledge base)
# Runs as separate service or embedded
```

### Key Design Decisions
1. **pgvector over separate vector DB for dialogue**：對話向量跟用戶數據同 DB，方便 JOIN 查詢（如：查某用戶所有對話的立場偏移軌跡）
2. **ChromaDB 僅用於 RAG**：知識庫資料跟業務數據性質不同，獨立管理合理
3. **Django Channels for WebSocket**：M4 對話室和 M5 CCND 即時更新都需要，統一技術選型
4. **Sentence-Transformers 做 embedding**：不依賴外部 API 做向量化，降低延遲和成本

### File Structure (Target)
```
bridgeus/
├── CLAUDE.md                  ← 你正在看的這個檔案
├── README.md
├── docs/
│   └── BridgeUs_API_Spec.md   ← 模組間 API 合約
├── backend/
│   ├── manage.py
│   ├── config/                ← Django project settings
│   │   ├── settings/
│   │   │   ├── base.py
│   │   │   ├── dev.py
│   │   │   └── prod.py
│   │   ├── urls.py
│   │   ├── asgi.py            ← Channels entry point
│   │   └── wsgi.py
│   ├── apps/
│   │   ├── auth_module/       ← M1
│   │   ├── stance/            ← M2
│   │   ├── matching/          ← M3
│   │   ├── dialogue/          ← M4
│   │   ├── nlp_analysis/      ← M5
│   │   └── summary/           ← M6
│   ├── core/                  ← Shared utilities, base models
│   └── requirements.txt
├── frontend/                  ← TBD (React)
├── scripts/                   ← Data collection, DB seeding
├── docker-compose.yml
└── .env.example
```

---

## Research Context (for AI assistant reference)

- **核心實驗設計**：30 人實驗組 + 30 人對照組（大學生）
- **對話模式**：Human-Human, Human-AI Agent, AI Agent-AI Agent
- **去極化指標**：立場偏移量（向量距離變化）、情緒分數變化、攻擊性詞彙減少、CCND 拓撲結構變化
- **準即時分析觸發**：每 90 分鐘 或 累積 200 字
- **關鍵文獻**：Combs et al. (2023) Nature Human Behaviour — DiscussIt platform; Argyle et al. (2023) PNAS — AI chat interventions

## H-H 對話模組
架構文件：./docs/HH_Architecture.md
目前進度：尚未開始
當前任務：建立 NLP Pipeline 基礎模組