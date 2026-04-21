# BridgeUs Django Backend

Django REST backend for the BridgeUs dialogue application.

This service currently provides:

- JWT authentication at `/api/token/` and `/api/token/refresh/`
- dialogue session creation at `/api/dialogue/sessions/`
- dialogue replies at `/api/dialogue/sessions/<session_id>/reply/`
- conversation CRUD endpoints at `/api/conversations/`

The frontend in `../BridgeUs` defaults to proxying API traffic to `http://127.0.0.1:8000`.

## Local run

```bash
uv sync
cp .env.example .env
./.venv/bin/python manage.py migrate
./.venv/bin/python manage.py runserver 8000
```

## Environment notes

- Set `DJANGO_SECRET_KEY` and `JWT_SIGNING_KEY` to distinct 32+ character values for non-debug deployments.
- Keep path-like settings in `.env` on project-relative values for local runs, for example `SQLITE_PATH=db.sqlite3` and `CHROMA_PERSIST_DIR=chroma_data`.
- The checked-in `.env.example` still uses `PORT=8005` for container-oriented runs. For the simplest local integration with the frontend, run Django on `8000` or set the frontend `VITE_PROXY_TARGET`.

If you deploy behind `https://dev.bridgeus.work`, keep that hostname in:

- `DJANGO_ALLOWED_HOSTS`
- `CORS_ALLOWED_ORIGINS`
- `CSRF_TRUSTED_ORIGINS`

## Docker run

1. Copy `.env.example` to `.env`.
2. Fill in `DJANGO_SECRET_KEY`, `JWT_SIGNING_KEY`, and `ANTHROPIC_API_KEY`.
3. Start the stack:

```bash
docker compose up --build
```

## Docker notes

- Dialogue session state uses Redis when `REDIS_URL` is enabled.
- SQLite, Chroma, and Hugging Face cache data are persisted in the `app_data` volume.
- The container entrypoint runs `migrate`, `collectstatic`, and then starts `gunicorn`.
- WhiteNoise serves static files for the admin and API deployment.
