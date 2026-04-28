# BridgeUs Agent Handoff

## Project Snapshot

- Current project path: `/Users/light/code/BridgeUs`
- Stack: Vite 8 + React 19 frontend, axios for API calls, React Router 7
- This workspace is **not** a normal git repo. There is a `.git.local-backup`, but no active `.git` directory.
- Current JS toolchain in this workspace:
  - `node v22.22.2`
  - `npm 10.9.7`
- `node_modules` and `package-lock.json` exist in the current path.
- `.venv` has already been rebuilt for the current path and no longer points to the old location.

## Important Files

- App shell and auth state: `src/App.jsx`
- API client and auth interceptor: `src/api/client.js`
- Login flow: `src/pages/LoginPage.jsx`
- Topic chat flow: `src/pages/TopicChat.jsx`
- Navbar logout UI: `src/components/Navbar.jsx`

## What Was Already Changed

### Auth / logout behavior

Recent work already added a usable logout path:

- Manual logout button in the navbar
- Shared auth cleanup that removes:
  - `access`
  - `refresh`
  - `bridgeus_user`
- Auto logout when JWT `exp` is reached
- Auto logout when protected API requests return `401`
- Login page error handling now distinguishes:
  - `401` wrong credentials
  - `5xx` backend unavailable
  - network / other failures

Implementation lives mainly in:

- `src/App.jsx`
- `src/api/client.js`
- `src/components/Navbar.jsx`
- `src/pages/LoginPage.jsx`

## Known Outstanding Findings

These are the main items still worth continuing from the previous review:

1. `src/pages/TopicChat.jsx`
   First message may be sent twice.
   Current flow sends the first text into session creation as `user_initial_argument`, then immediately sends the same text again to `/reply/`.
   If backend persists `user_initial_argument`, the first user message and/or first assistant response can duplicate.

2. `src/pages/TopicChat.jsx`
   IME handling is still incomplete.
   The input currently sends on plain `Enter` and does not guard against composition state, so Chinese input method selection can accidentally send half-finished text.

3. `src/api/client.js`
   The app now auto-logs out on `401`, but refresh-token retry logic is **not** implemented.
   Current behavior is deliberate fallback: expired auth leads to logout instead of silent refresh.

4. `src/pages/TopicChat.jsx`
   `mapHistoryToMessages()` assumes backend uses role `'agent'`.
   If backend returns `'assistant'` or `'system'`, UI mapping may be wrong.

## Current Behavioral Notes

- Topic list in `src/App.jsx` is still mocked in the frontend.
- Login expects backend token endpoint at `/api/token/`.
- Dialogue flow expects:
  - `POST /api/dialogue/sessions/`
  - `POST /api/dialogue/sessions/:id/reply/`
- Vite proxy config is in `vite.config.js`.

## Suggested Next Steps

Recommended order for the next agent:

1. Fix the duplicate first-message flow in `src/pages/TopicChat.jsx`.
2. Fix IME-safe Enter handling in the same chat input.
3. Confirm backend response contract for chat history roles.
4. Decide whether product wants:
   - explicit logout-on-expiry only, or
   - full refresh-token retry flow
5. Run verification after any change:
   - `npm run dev`
   - `npm run build`
   - `npm run lint`

## Environment Notes

- If the project is moved again, recreate `.venv` rather than copying old path-bound scripts.
- IDE metadata under `.idea/` is local-machine state and can be ignored or regenerated.
- Because this is not an active git repo, do not rely on `git diff` for change tracking.
