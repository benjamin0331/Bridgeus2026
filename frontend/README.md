# BridgeUs Frontend

React + Vite frontend for BridgeUs. It supports backend-driven topic/survey loading, AI dialogue mode, and anonymous human matching mode.

## Location

```text
/Users/light/code/frontend
```

The active git repo is the parent monorepo at `/Users/light/code`, not this folder alone.

## Requirements

- Node.js compatible with the current lockfile/tooling
- npm
- Backend running on `http://127.0.0.1:8005` by default

## Local Development

```bash
npm install
npm run dev
```

Vite proxies both HTTP and WebSocket traffic:

- `/api/*` -> `VITE_PROXY_TARGET` or `http://127.0.0.1:8005`
- `/ws/*` -> `VITE_PROXY_TARGET` or `http://127.0.0.1:8005`

If the backend runs elsewhere:

```bash
VITE_PROXY_TARGET=http://127.0.0.1:8005 npm run dev
```

## Main Files

- App shell and routes: `src/App.jsx`
- API client and auth handling: `src/api/client.js`
- Login + forgot-password flow (view switch, same card): `src/pages/LoginPage.jsx`
- Register: `src/pages/RegisterPage.jsx`
- Topic / AI dialogue / matching UI: `src/pages/TopicChat.jsx`
- Topic chat styles: `src/pages/TopicChat.css`
- Vite proxy: `vite.config.js`

## Backend Contract

Auth:

- `POST /api/token/` (`username` may be the account's username or email)
- `POST /api/token/refresh/`
- `POST /api/register/` (needs a verified email first)
- `POST /api/email-verification/request/` · `POST /api/email-verification/confirm/`
- `POST /api/me/email/verify/request/` · `POST /api/me/email/verify/confirm/`
- `POST /api/password-reset/request/` · `POST /api/password-reset/confirm/`

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
- `WS /ws/matching/rooms/<room_id>/`

## Current UX Behavior

- Topic and survey data are loaded from the backend.
- Matching mode requires completing the backend survey first.
- If backend returns `ai_recommended`, the UI should guide the user to AI dialogue.
- Matching chat is anonymous; the current user is displayed on the right side.
- Room messages are persisted by the backend.
- The UI polls matching status while waiting and fetches room message snapshots from `/api/matching/rooms/<room_id>/messages/` while also using WebSocket for live delivery. Repeated backend 200 logs for this endpoint are expected during an open room.
- The right-side semantic tree panel does not classify messages locally. It loads `GET /api/matching/rooms/<room_id>/semantic-tree/` and triggers `POST /api/matching/rooms/<room_id>/semantic-tree/analyze/` after new room messages arrive; backend Gemini analysis owns the prompt, schema validation, and tree merge behavior.
- AI 開場: on entering a room `TopicChat.jsx` requests the opening once. H-AI renders it as the first agent bubble (it arrives inside the session `history`); H-H renders `match-opening-card` above the transcript and also accepts the WebSocket `match_opening` broadcast triggered by whoever entered first. A failed request is silent — the participant just starts the conversation themselves.
- When backend `H_H_AI_ASSIST_ENABLED=true`, matching WebSocket may emit `match_system_prompt` and `match_ai_suggestion`; `TopicChat.jsx` renders these as prompt/suggestion cards with accept, modify, or ignore actions. Backend may fail open and relay messages without intervention if NLP inference exceeds `H_H_AI_ASSIST_TIMEOUT_SECONDS`.
- Closing or leaving pages sends best-effort cancel/leave requests to avoid ghost queue entries.

## Verification

```bash
npm run lint
npm run build
```

In sandboxed environments, `npm run build` may need permission to write Vite temporary files under `node_modules/.vite-temp`.

## Docker Test Deployment

The frontend Docker setup builds static files and serves them through nginx. It forwards `/api/*` and `/ws/*` to the backend.

Default assumptions:

- Backend is reachable from Docker as `host.docker.internal:8005`.
- Browser opens the frontend on `http://localhost:8080`.

```bash
docker compose up --build
```

Override backend target if needed:

```bash
BACKEND_HOST=host.docker.internal BACKEND_PORT=8005 docker compose up --build
```

On Linux, you may need:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```
