# Workspace Guide

## Current Layout
- Root workspace: `/Users/light/code`.
- Django backend: `backend/`. Settings live in `backend/BridgeUs_Django/settings.py`, API endpoints live in `backend/api/`, matching logic lives in `backend/apps/matching/services/`, and knowledge-base scripts live in `backend/scripts/`.
- React frontend: `frontend/`. Routing starts in `frontend/src/App.jsx`, API access is centralized in `frontend/src/api/client.js`, and the dialogue/matching UI lives in `frontend/src/pages/TopicChat.jsx`.
- Old folders such as `BridgeUs_Django_withLLM` and `BridgeUs` were removed from the active project. Do not edit or recreate those paths unless the user explicitly asks for archival recovery.

## Git State And Remotes
- `/Users/light/code` is the active git worktree.
- Current working branch used by the user: `feat/Light`.
- Main monorepo remote: `origin https://github.com/bridgeus2026/Bridgeus2026.git`.
- Backend-only mirror remote may exist as `light-backend https://github.com/Bridge-US2026/Light_Django_backend.git`; do not push the whole monorepo there.
- Before any commit or push, run:
  - `git rev-parse --show-toplevel`
  - `git status -sb`
  - `git remote -v`
- The user usually expects `可以git了` / `幫我git上去` to mean stage, commit, and push the intended current work to `origin/feat/Light`, but still inspect the dirty tree first.

## Backend
- Use `uv` from `backend/`.
- Common commands:
  - `uv sync`
  - `uv run python manage.py check`
  - `uv run python manage.py migrate`
  - `uv run python manage.py createsuperuser`
  - `uv run python manage.py runserver 0.0.0.0:8005 --noreload`
  - `uv run uvicorn BridgeUs_Django.asgi:application --host 0.0.0.0 --port 8005`
  - `uv run python scripts/build_knowledge_base.py --data-dir data/nuclear_energy --collection nuclear_energy_all`
- Environment is loaded from `backend/.env`. Do not commit real secrets or print secret values.
- `DB_ENGINE=sqlite` is fine for local smoke tests. Use `DB_ENGINE=postgres` with PostgreSQL + pgvector for realistic matching/chat persistence testing.
- Important env flags:
  - `PORT=8005`
  - `DB_CONN_MAX_AGE=0` to avoid exhausting small Postgres deployments during local/server mixed testing.
  - `MATCHING_ALLOW_SAME_STANCE_FALLBACK=false` for production heterogeneous matching; set `true` only for test convenience.
  - `MATCH_ROOM_IDLE_TIMEOUT_SECONDS=600` closes inactive matching rooms after 10 minutes.
  - `USE_REDIS_CACHE=true` is required if dialogue session cache must survive multiple workers/containers.
  - `USE_REDIS_CHANNEL=true` enables Redis channel layer for multi-process WebSocket deployment.

## Backend Behavior Hot Spots
- Topic and survey config: `backend/api/dialogue_topics.py`.
- Stance scoring and API views: `backend/api/views.py`.
- Matching queue, restart, cancellation, stale queue cleanup, and idle room cleanup: `backend/apps/matching/services/matcher.py`.
- Heterogeneous matching weights and candidate ranking: `backend/apps/matching/services/matching_algorithm.py`.
- Q9 semantic embedding bridge: `backend/apps/matching/services/semantic.py`.
- WebSocket consumers for AI dialogue and matching room chat: `backend/api/consumers.py`.
- Persistent models for stance profiles, queue entries, matches, AI turns, and match messages: `backend/api/models.py`.

## Frontend
- Run frontend commands from `frontend/`.
- Common commands:
  - `npm install`
  - `npm run dev`
  - `npm run lint`
  - `npm run build`
- Vite proxies `/api` and `/ws` to `VITE_PROXY_TARGET`, defaulting to `http://127.0.0.1:8005`.
- Matching and AI dialogue UI are both in `frontend/src/pages/TopicChat.jsx`.
- Logout/session cleanup should clear `access`, `refresh`, and `bridgeus_user` together.

## Verification
- Backend smoke checks:
  - `uv run python manage.py check`
  - `DB_ENGINE=sqlite uv run python manage.py test api --keepdb --noinput`
  - `DB_ENGINE=sqlite uv run pytest api/tests_websocket.py chat/tests.py chat/tests_filter.py`
- Frontend smoke checks:
  - `npm run lint`
  - `npm run build`
- In sandboxed sessions, `uv` cache access or Vite temp writes may require elevated execution. Do not treat that as an application failure until rerun outside the sandbox.

## Current Product Notes
- Active topic is `topic_id=102`, title `台灣核能議題討論`, collection `nuclear_energy_all`.
- Both AI mode and human matching mode use backend-provided survey questions.
- Likert scoring uses Q2/Q4/Q5/Q6 reverse scoring and produces a 1-7 stance score.
- Default human matching is heterogeneous: support pairs with oppose; neutral users receive `ai_recommended` unless fallback is enabled.
- Q9 is stored and embedded for semantic matching; Q10 is currently stored as raw survey context.
- Matching rooms are anonymous in the UI and API payloads.
- Matching room messages and AI dialogue turns are persisted in the database.
- Matching rooms auto-close after `MATCH_ROOM_IDLE_TIMEOUT_SECONDS` without conversation activity.

## Working Rules
- Prefer targeted edits and preserve user changes in dirty worktrees.
- Do not use destructive git commands unless the user explicitly asks and approval is clear.
- Do not reintroduce old `BridgeUs_Django_withLLM` or `BridgeUs` paths into docs or scripts.
- If the user asks for review, lead with concrete findings and file references.
- If the user asks to change matching behavior, keep the editable algorithm surface in `backend/apps/matching/services/matching_algorithm.py`.
- Prefer `backend/docker-compose.yml` and `frontend/docker-compose.yml` for current Docker testing; root `docker-compose.yml` is older and should be reviewed before relying on it.
