# Workspace Guide

## Layout
- `BridgeUs_Django_withLLM`: Django backend. Settings live in `BridgeUs_Django/settings.py`, API endpoints live in `api/`, and the dialogue agent code lives in `apps/matching/services/`.
- `BridgeUs`: React + Vite frontend. Routing starts in `src/App.jsx`, API access is centralized in `src/api/client.js`, and the dialogue UI lives in `src/pages/TopicChat.jsx`.

## Backend
- The backend uses `uv`, but this workspace already contains a ready `.venv`. In sandboxed runs, prefer `./.venv/bin/python` from `BridgeUs_Django_withLLM/`.
- Common commands:
  - `./.venv/bin/python manage.py runserver`
  - `./.venv/bin/python manage.py test`
  - `./.venv/bin/python scripts/build_knowledge_base.py --collection <name> --data-dir data/<dir>`
- Secrets are loaded from `BridgeUs_Django_withLLM/.env`. Do not commit secrets or print sensitive values in reviews.

## Frontend
- Run frontend commands from `BridgeUs/`.
- Common commands:
  - `npm run dev`
  - `npm run build`
  - `./node_modules/.bin/eslint src`
- Vite proxies `/api` to `VITE_PROXY_TARGET`, defaulting to `http://127.0.0.1:8000`.

## Verification
- Backend smoke check: `./.venv/bin/python manage.py test`
- Frontend smoke checks:
  - `./node_modules/.bin/eslint src`
  - `npm run build`
- In sandboxed sessions, Vite may need permission to write temporary files under `node_modules/.vite-temp` during `npm run build`.

## Integration Notes
- Frontend login expects backend JWT endpoints at `/api/token/` and `/api/token/refresh/`.
- Dialogue chat expects `/api/dialogue/sessions/` and `/api/dialogue/sessions/<session_id>/reply/`.
- The issue list in `BridgeUs/src/App.jsx` is currently mocked in the frontend; there is no matching issue-list backend endpoint in this workspace yet.

## Working Rules
- Prefer targeted edits. Avoid touching duplicate scratch files such as `* 2.py`, `* 2.jsx`, or extra `uv` lockfiles unless the task explicitly calls for cleanup.
- When reviewing auth or session behavior, inspect both `BridgeUs_Django_withLLM/api/views.py` and `BridgeUs/src/api/client.js` because the flow spans both projects.
- Current review hot spots:
  - Backend data ownership and permission boundaries in `BridgeUs_Django_withLLM/api/views.py`
  - Backend environment safety in `BridgeUs_Django_withLLM/BridgeUs_Django/settings.py`
  - Frontend effect-heavy auth/session logic in `BridgeUs/src/App.jsx` and `BridgeUs/src/pages/LoginPage.jsx`
  - Frontend chat reset/state flow in `BridgeUs/src/pages/TopicChat.jsx`
