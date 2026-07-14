# Take A Bridge (橋得攏)

Take A Bridge is a heterogeneous-viewpoint dialogue platform for structured discussion across different stances. The current implementation focuses on the Taiwan nuclear-energy topic and supports two user-facing paths:

- Human-AI dialogue with an AI agent taking the opposing stance.
- Anonymous human-human matching based on survey stance scores and optional Q9 semantic distance.

The project is currently organized as a monorepo:

```text
/Users/light/code
├── backend/   # Django REST + Channels backend
├── frontend/  # React + Vite frontend
├── data/      # shared/source data
└── docs/      # project notes
```

## Current Git Layout

- Active repo root: `/Users/light/code`
- Main remote: `origin https://github.com/bridgeus2026/Bridgeus2026.git`
- Active working branch: `feat/Light`
- Backend-only mirror remote may exist as `light-backend https://github.com/Bridge-US2026/Light_Django_backend.git`

Before git operations, verify the target:

```bash
git rev-parse --show-toplevel
git status -sb
git remote -v
```

## Tech Stack

- Backend: Python 3.12+, Django 6, Django REST Framework, Django Channels, SimpleJWT
- Database: SQLite for local smoke tests; PostgreSQL + pgvector for server/realistic testing
- Matching/NLP: pgvector, scipy cosine distance, sentence-transformers embedding bridge
- RAG/AI: Anthropic, LangChain, ChromaDB, sentence-transformers
- Frontend: React 19, React Router 7, Vite 8, axios
- Deployment: Docker Compose, uvicorn ASGI, optional Redis cache/channel layer

## Backend Quick Start

```bash
cd backend
uv sync
cp .env.example .env
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python scripts/build_knowledge_base.py --data-dir data/nuclear_energy --collection nuclear_energy_all
uv run python manage.py warm_nlp_models
uv run uvicorn take_a_bridge.asgi:application --host 0.0.0.0 --port 8005
```

For simple HTTP-only development, this also works:

```bash
uv run python manage.py runserver 0.0.0.0:8005 --noreload
```

Use ASGI/uvicorn when testing WebSocket dialogue or matching-room chat.

## Frontend Quick Start

```bash
cd frontend
npm install
npm run dev
```

Vite proxies `/api` and `/ws` to `VITE_PROXY_TARGET`, defaulting to `http://127.0.0.1:8005`.

Open the frontend at the Vite dev-server URL, usually:

```text
http://localhost:5173
```

## Important Environment Values

In `backend/.env`:

```env
PORT=8005
DJANGO_DEBUG=true
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0,dev.bridgeus.work
CORS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,https://dev.bridgeus.work
CSRF_TRUSTED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,https://dev.bridgeus.work

DB_ENGINE=sqlite
# DB_ENGINE=postgres
DB_NAME=bridgeus
DB_USER=postgres
DB_PASSWORD=replace-me
DB_HOST=127.0.0.1
DB_PORT=5432
DB_CONN_MAX_AGE=0

MATCHING_ALLOW_SAME_STANCE_FALLBACK=false
MATCH_ROOM_IDLE_TIMEOUT_SECONDS=600
H_H_AI_ASSIST_ENABLED=false
H_H_AI_ASSIST_TIMEOUT_SECONDS=2
PRELOAD_NLP_MODELS=false
GEMINI_API_KEY=replace-me
GEMINI_MODEL=gemini-2.5-flash
GEMINI_API_TIMEOUT_SECONDS=20
SEMANTIC_TREE_ANALYZE_BATCH_SIZE=5
USE_REDIS_CACHE=false
USE_REDIS_CHANNEL=false
```

For production or shared server use, set `DJANGO_SECRET_KEY` and `JWT_SIGNING_KEY` to separate values with at least 32 characters.

## Database Notes

Local smoke testing can use SQLite. For the server or matching algorithm testing with semantic vectors, use PostgreSQL with pgvector enabled:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Then run:

```bash
cd backend
uv run python manage.py migrate
```

## Current API Surface

Authentication:

- `POST /api/token/`
- `POST /api/token/refresh/`

Topics and surveys:

- `GET /api/dialogue/topics/`
- `GET /api/dialogue/topics/<topic_id>/survey/`

AI dialogue:

- `POST /api/dialogue/sessions/`
- `POST /api/dialogue/sessions/<session_id>/reply/`
- `WS /ws/dialogue/<session_id>/`

Human matching:

- `POST /api/matching/join/`
- `GET /api/matching/status/?topic_id=102`
- `POST /api/matching/cancel/`
- `GET /api/matching/rooms/<room_id>/messages/`
- `POST /api/matching/rooms/<room_id>/messages/`
- `POST /api/matching/rooms/<room_id>/leave/`
- `GET /api/matching/rooms/<room_id>/semantic-tree/`
- `POST /api/matching/rooms/<room_id>/semantic-tree/analyze/`
- `WS /ws/matching/rooms/<room_id>/`

## Matching Behavior

- Topic `102` is currently `台灣核能議題討論`.
- Likert scoring uses Q2/Q4/Q5/Q6 reverse scoring.
- Stance score `S` is in `[1, 7]`.
- `S > 4.5`: support.
- `S < 3.5`: oppose.
- `3.5 <= S <= 4.5`: neutral.
- Default production matching only pairs support with oppose.
- Neutral users receive `ai_recommended` and should be guided to AI dialogue.
- Test-only fallback can be enabled with `MATCHING_ALLOW_SAME_STANCE_FALLBACK=true`.
- Q9 is embedded for semantic matching; Q10 is stored but not scored yet.
- Matching rooms are anonymous and auto-close after 10 minutes of no conversation activity by default.
- Matching room semantic trees are built by backend Gemini analysis, not frontend keyword matching. `GET /semantic-tree/` returns the persisted `DialogueMatch.stats["semantic_tree"]` tree; `POST /semantic-tree/analyze/` analyzes unprocessed room messages in small batches and stores applied nodes/history. Missing `GEMINI_API_KEY` returns a semantic-tree error only; chat message delivery remains unaffected.
- Human-human AI assistance is feature-flagged with `H_H_AI_ASSIST_ENABLED=true`; when enabled, WebSocket matching rooms can show content-block prompts and AI rephrase/direction/redirect suggestions. `H_H_AI_ASSIST_TIMEOUT_SECONDS` keeps slow NLP inference or first-time model downloads from blocking chat message delivery. Run `uv run python manage.py warm_nlp_models` before starting uvicorn, or set `PRELOAD_NLP_MODELS=true` in Docker, to download and warm models ahead of traffic. Repeated `GET /api/matching/rooms/<room_id>/messages/` logs during chat are the frontend polling room snapshots and are expected.

## NLP Model Warmup

If H-H AI assist is enabled, preload NLP models before testing chat intervention so first-use Hugging Face downloads do not happen during a live room:

```bash
cd backend
uv run python manage.py warm_nlp_models
```

This warms the Q9/message embedding model and the emotion model. Use `--skip-embedding` or `--skip-emotion` when debugging only one side. Docker can run the same step before uvicorn with `PRELOAD_NLP_MODELS=true`.

## Verification

Backend:

```bash
cd backend
uv run python manage.py check
DB_ENGINE=sqlite uv run python manage.py test api --keepdb --noinput
DB_ENGINE=sqlite uv run pytest api/tests_websocket.py chat/tests*.py
```

Frontend:

```bash
cd frontend
npm run lint
npm run build
```

## Docker

Use the service-specific compose files for the current app layout. The root `docker-compose.yml` is an older integration draft and should be reviewed before use.

Backend test container:

```bash
cd backend
docker compose up --build
```

Frontend test container:

```bash
cd frontend
docker compose up --build
```

Current defaults:

- Backend: `http://localhost:8005`
- Frontend: `http://localhost:8080`
- Frontend container forwards `/api` and `/ws` to `host.docker.internal:8005` unless overridden.

## Project Context

Take A Bridge is an NSTC undergraduate research project for structured depolarization dialogue. The research period is planned for 2026/07 - 2027/02. Advisor: 張昭憲（淡江大學資管系副教授）.

Current engineering priority: make the survey, matching, AI dialogue, and anonymous chat lifecycle stable before adding long-running post-dialogue analysis and CCND visualization.

Merge note: `feat/Light` is the source of truth for matching-room architecture. Do not directly merge `feat/benjamin`; selectively port compatible H-H AI-assist services into the current `DialogueMatch` / `MatchMessage` stack.

## System Modules

| Module | Name | Current status |
|--------|------|----------------|
| M1 | User Auth | JWT login/admin account flow is implemented. |
| M2 | Topic Selection & Stance Measurement | Backend topic/survey config and Likert scoring are implemented for topic 102. |
| M3 | Heterogeneous Matching & AI Agent Generation | Matching queue, stance score, Q9 semantic hook, and AI dialogue agent are implemented. |
| M4 | Real-time Dialogue Room | WebSocket matching room and persisted messages are implemented; rooms auto-close when idle. |
| M5 | NLP Analysis & CCND Generation | H-H AI assist, emotion timeout fail-open, NLP model warmup, and research suggestion records are implemented; CCND generation is planned. |
| M6 | Post-Dialogue Summary & Knowledge Base | Knowledge-base build flow exists; post-dialogue summary is planned. |

## Team

| Member | Role |
|--------|------|
| 賴則名 (Benjamin) | Project Manager |
| 伍晨安 | Backend / matching / AI agent development |
| 陳彩希 | Frontend UI/UX |
| 黃筱筑 | QA & optimization |
| 葉錦諦 | Data engineering |
