# BridgeUs Backend

Django backend for BridgeUs. It provides JWT auth, backend-managed topic/survey configuration, AI dialogue sessions, anonymous matching, WebSocket chat, persistence, and the RAG dialogue agent.

## Location

```text
/Users/light/code/backend
```

This backend currently lives inside the `/Users/light/code` monorepo. The monorepo remote is:

```text
origin https://github.com/bridgeus2026/Bridgeus2026.git
```

A backend-only mirror remote may exist as `light-backend`, but do not push the whole monorepo there.

## Requirements

- Python 3.12+
- uv
- SQLite for quick local smoke tests
- PostgreSQL + pgvector for server/realistic matching tests
- Redis only when using multi-worker cache/channel deployment

## Setup

```bash
uv sync
cp .env.example .env
uv run python manage.py migrate
uv run python manage.py createsuperuser
```

Build the current nuclear-energy knowledge base:

```bash
uv run python scripts/build_knowledge_base.py --data-dir data/nuclear_energy --collection nuclear_energy_all
```

Run ASGI server on the expected backend port:

```bash
uv run uvicorn BridgeUs_Django.asgi:application --host 0.0.0.0 --port 8005
```

HTTP-only fallback:

```bash
uv run python manage.py runserver 0.0.0.0:8005 --noreload
```

Use uvicorn when testing WebSockets.

## Environment

Copy `backend/.env.example` to `backend/.env` and review these values:

```env
DJANGO_SECRET_KEY=replace-with-at-least-32-characters
JWT_SIGNING_KEY=replace-with-at-least-32-characters
DJANGO_DEBUG=true
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0,dev.bridgeus.work
PORT=8005

DB_ENGINE=sqlite
DB_CONN_MAX_AGE=0

MATCHING_ALLOW_SAME_STANCE_FALLBACK=false
MATCH_ROOM_IDLE_TIMEOUT_SECONDS=600
H_H_AI_ASSIST_ENABLED=false

USE_REDIS_CACHE=false
USE_REDIS_CHANNEL=false
```

For PostgreSQL:

```env
DB_ENGINE=postgres
DB_NAME=bridgeus
DB_USER=postgres
DB_PASSWORD=replace-me
DB_HOST=127.0.0.1
DB_PORT=5432
```

Enable pgvector in the database:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

## Main Files

- Settings: `BridgeUs_Django/settings.py`
- HTTP routes: `api/urls.py`
- API views and stance scoring: `api/views.py`
- WebSocket consumers: `api/consumers.py`
- Persistent models: `api/models.py`
- Topic/survey config: `api/dialogue_topics.py`
- Matching lifecycle: `apps/matching/services/matcher.py`
- Matching score algorithm: `apps/matching/services/matching_algorithm.py`
- H-H AI assist services: `apps/matching/services/hh_ai.py` and `apps/matching/services/hh_analysis.py`
- Q9 embedding bridge: `apps/matching/services/semantic.py`
- AI dialogue agent: `apps/matching/services/ai_agent.py`
- Knowledge-base builder: `scripts/build_knowledge_base.py`

## API Endpoints

Auth:

- `POST /api/token/`
- `POST /api/token/refresh/`

Topics/surveys:

- `GET /api/dialogue/topics/`
- `GET /api/dialogue/topics/<topic_id>/survey/`

AI dialogue:

- `POST /api/dialogue/sessions/`
- `POST /api/dialogue/sessions/<session_id>/reply/`
- `WS /ws/dialogue/<session_id>/`

Matching:

- `POST /api/matching/join/`
- `GET /api/matching/status/?topic_id=102`
- `POST /api/matching/cancel/`
- `GET /api/matching/rooms/<room_id>/messages/`
- `POST /api/matching/rooms/<room_id>/messages/`
- `POST /api/matching/rooms/<room_id>/leave/`
- `WS /ws/matching/rooms/<room_id>/`

## Matching Details

- Current topic: `102`, `台灣核能議題討論`.
- Likert reverse items: Q2, Q4, Q5, Q6.
- `S > 4.5`: support.
- `S < 3.5`: oppose.
- `3.5 <= S <= 4.5`: neutral.
- Default matching only pairs support with oppose.
- Neutral users return `ai_recommended` and are not queued for human matching unless fallback is enabled.
- `MATCHING_ALLOW_SAME_STANCE_FALLBACK=true` is for testing only.
- Q9 embedding is stored in `UserStanceProfile.q9_embedding` using pgvector when available.
- `DialogueMatch` stores likert distance, semantic distance, weighted match score, and algorithm version.
- Matching rooms auto-close after `MATCH_ROOM_IDLE_TIMEOUT_SECONDS` without conversation activity.
- H-H AI assistance is off by default. Set `H_H_AI_ASSIST_ENABLED=true` to enable content-block prompts, emotion rephrase suggestions, topic redirects, and research suggestion records for WebSocket matching rooms.

## Admin

Create an admin user:

```bash
uv run python manage.py createsuperuser
```

Then open:

```text
http://localhost:8005/admin/
```

Password policy can be loosened for local/admin testing through env flags:

```env
DJANGO_PASSWORD_MIN_LENGTH=1
ALLOW_COMMON_PASSWORDS=true
ALLOW_NUMERIC_PASSWORDS=true
```

Do not use permissive password settings in production.

## Verification

```bash
uv run python manage.py check
DB_ENGINE=sqlite uv run python manage.py test api --keepdb --noinput
DB_ENGINE=sqlite uv run pytest api/tests_websocket.py chat/tests.py chat/tests_filter.py
```

The API test suite intentionally logs one fake AI-provider exception to verify stable 503 handling.

## Docker Notes

Run from this `backend/` directory:

```bash
docker compose up --build
```

Notes:

- Backend port is `8005`.
- `backend/docker-compose.yml` starts Redis and persists SQLite/Chroma/Hugging Face data in the `app_data` volume.
- The container entrypoint runs migrations and collectstatic before starting ASGI.
- Use Redis cache if running more than one worker/container and dialogue session state must persist across workers.
- Use Redis channel layer if WebSocket traffic spans multiple backend processes.
