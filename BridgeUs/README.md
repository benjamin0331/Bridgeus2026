# BridgeUs Frontend

React + Vite frontend for BridgeUs. This app currently handles:

- JWT login
- mocked issue/topic cards in `src/App.jsx`
- dialogue chat pages that call the backend session API
- the knowledge base page

In this monorepo, the backend lives in `../BridgeUs_Django_withLLM`.

## Local development

```bash
npm install
npm run dev
```

The Vite dev server proxies `/api/*` to `http://127.0.0.1:8000` unless `VITE_PROXY_TARGET` is set.

Expected backend endpoints:

- `POST /api/token/`
- `POST /api/token/refresh/`
- `POST /api/dialogue/sessions/`
- `POST /api/dialogue/sessions/<session_id>/reply/`

## Tooling notes

- Frontend runtime dependencies are managed through `package.json`.
- This folder also keeps `pyproject.toml` and `uv.lock` alongside the JavaScript toolchain because the workspace uses `uv` as part of the project setup.

## Docker test deployment

```bash
docker compose up --build
```

Then open `http://localhost:8080`.

The compose setup forwards `/api/*` to:

- `BACKEND_HOST=host.docker.internal`
- `BACKEND_PORT=8000`

Override them at startup if needed:

```bash
BACKEND_HOST=host.docker.internal BACKEND_PORT=8001 docker compose up --build
```
