# BridgeUs Frontend Agent Handoff

## Current Snapshot

- Frontend path: `/Users/light/code/frontend`.
- Active git repo: parent monorepo `/Users/light/code`.
- Main branch used by the user: `feat/Light`.
- Stack: Vite 8, React 19, React Router 7, axios.
- Backend default target: `http://127.0.0.1:8005`.

## Important Files

- `src/App.jsx`: app shell, routes, auth state.
- `src/api/client.js`: axios client and JWT/session cleanup.
- `src/pages/LoginPage.jsx`: login UX.
- `src/pages/TopicChat.jsx`: AI dialogue mode, matching mode, survey modal, chat UI, WebSocket and polling lifecycle.
- `src/pages/TopicChat.css`: TopicChat styling and mobile responsive behavior.
- `vite.config.js`: `/api` and `/ws` proxy configuration.

## Current Behavior

- Topic list and survey questions come from backend endpoints.
- AI dialogue uses `POST /api/dialogue/sessions/`, `POST /reply/`, and WebSocket streaming where available.
- Matching mode posts survey answers to `POST /api/matching/join/`.
- Matching status is polled through `GET /api/matching/status/?topic_id=102`.
- Matching messages are fetched/persisted through `/api/matching/rooms/<room_id>/messages/` and live updates use `WS /ws/matching/rooms/<room_id>/`.
- Matching WebSocket can emit `match_system_prompt` and `match_ai_suggestion` when backend H-H AI assist is enabled; keep suggestion-card behavior in `TopicChat.jsx`.
- Matching participants are anonymous in the UI.
- Current user's own messages should render on the right.
- Page leave/unload sends best-effort cancel/leave requests to reduce ghost matching state.
- Backend also closes idle matching rooms after 10 minutes with no conversation activity.

## Backend Assumptions

- Backend runs on port `8005`.
- Vite proxy defaults to `http://127.0.0.1:8005`.
- Deployed backend host may be `https://dev.bridgeus.work`.
- JWT endpoints are `/api/token/` and `/api/token/refresh/`.
- Matching neutral state can return `ai_recommended`.

## Verification

Run from `/Users/light/code/frontend`:

```bash
npm run lint
npm run build
```

If checking full integration locally, also run backend ASGI from `/Users/light/code/backend`:

```bash
uv run uvicorn BridgeUs_Django.asgi:application --host 0.0.0.0 --port 8005
```

## Git Notes

- Do not run git commands from an assumed standalone frontend repo.
- Use `/Users/light/code` as the git root.
- Before staging, inspect `git status -sb` from the root because backend and frontend changes often ship together.
