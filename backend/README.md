# Light_Django_backend

Backend service for the BridgeUs dialogue application.

The sibling frontend repo at `../BridgeUs` currently expects:

- Vite dev server on `http://localhost:5173`
- Frontend Docker test deployment on `http://localhost:8080`
- Django backend on `http://127.0.0.1:8005` or `http://localhost:8005`
- Deployed backend host `dev.bridgeus.work`

## Local run

```bash
source .venv/bin/activate
python manage.py migrate
python manage.py runserver 8005 --noreload
```

For production, set `DJANGO_SECRET_KEY` and `JWT_SIGNING_KEY` to separate values
with at least 32 characters. Short keys will trigger JWT warnings and are not
safe for deployment.

If you deploy behind `https://dev.bridgeus.work`, keep that hostname in:

- `DJANGO_ALLOWED_HOSTS`
- `CORS_ALLOWED_ORIGINS`
- `CSRF_TRUSTED_ORIGINS`

For local CLI use, keep the path variables in `.env` on project-relative paths such as:

```bash
PORT=8005
SQLITE_PATH=db.sqlite3
CHROMA_PERSIST_DIR=chroma_data
HF_HOME=.cache/huggingface
```

If your `.env` contains Docker-only paths like `/data/chroma`, bare-metal runs will fail unless that directory exists and is writable.

When the virtualenv is already activated, prefer:

```bash
python -m apps.matching.services.ai_agent
```

instead of `uv run python -m ...`.

## Docker run

1. Copy `.env.example` to `.env` and fill in `DJANGO_SECRET_KEY` and `ANTHROPIC_API_KEY`.
2. Start the stack:

```bash
docker compose up --build
```

The Django API will be available at `http://localhost:8005`.

## Docker notes

- Dialogue session state uses Redis when `REDIS_URL` is set.
- SQLite, Chroma, and Hugging Face cache data are persisted in the `app_data` Docker volume.
- The container entrypoint runs `migrate`, `collectstatic`, and then starts `gunicorn`.
- Admin static files are served by WhiteNoise in the backend container, so
  `/admin/` should render correctly without a separate nginx sidecar.
