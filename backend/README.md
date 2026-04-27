# Light_Django_backend

Backend service for the BridgeUs dialogue application.

This repository contains:

- Django REST API endpoints for auth and dialogue sessions
- The RAG dialogue agent under `apps/matching/services/ai_agent.py`
- Knowledge-base build scripts and source data under `scripts/` and `data/`

## Git Notes

Current workspace note:

- As of April 22, 2026, `/Users/light/code` is not an active git working tree because `.git` is absent.
- The old git metadata directory currently exists as `/Users/light/code/.git.removed-20260422`.
- Any future chat that needs git operations should check the workspace state first instead of assuming `/Users/light/code` is still a live repo.

Last known git layout before `.git` was removed:

- Intended main repo root: `/Users/light/code`
- Main repo remote: `https://github.com/bridgeus2026/Bridgeus2026.git`
- Main working branch used in previous sessions: `feat/Light`
- Backend-only mirror repo: `https://github.com/Bridge-US2026/Light_Django_backend.git`
- The backend-only repo should receive only the contents of `backend/`, not the whole monorepo.

Recommended first checks for future git work:

```bash
ls -la /Users/light/code
git -C /Users/light/code status
```

If `/Users/light/code/.git` is still missing, a future chat should inspect `.git.removed-20260422`
or re-clone from GitHub before attempting `pull`, `push`, or branch operations.

The sibling frontend repo at `../BridgeUs` currently expects:

- Vite dev server on `http://localhost:5173`
- Frontend Docker test deployment on `http://localhost:8080`
- Django backend on `http://127.0.0.1:8005` or `http://localhost:8005`
- Deployed backend host `dev.bridgeus.work`

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) installed
- Git

## Environment

Copy the example environment file:

```bash
cp .env.example .env
```

At minimum, review and update these values in `.env` before running the app:

```bash
DJANGO_SECRET_KEY=replace-with-at-least-32-characters
JWT_SIGNING_KEY=replace-with-at-least-32-characters
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0,dev.bridgeus.work
PORT=8005
```

For production, set `DJANGO_SECRET_KEY` and `JWT_SIGNING_KEY` to separate values
with at least 32 characters. Short keys will trigger JWT warnings and are not
safe for deployment.

If you deploy behind `https://dev.bridgeus.work`, keep that hostname in:

- `DJANGO_ALLOWED_HOSTS`
- `CORS_ALLOWED_ORIGINS`
- `CSRF_TRUSTED_ORIGINS`

For local CLI use, keep the path variables on project-relative paths such as:

```bash
PORT=8005
SQLITE_PATH=db.sqlite3
CHROMA_PERSIST_DIR=chroma_data
HF_HOME=.cache/huggingface
```

If your `.env` contains Docker-only paths like `/data/chroma`, bare-metal runs
will fail unless that directory exists and is writable.

## Server Setup From Scratch

Clone the repository on the server:

```bash
git clone https://github.com/Bridge-US2026/Light_Django_backend.git
cd Light_Django_backend
```

Sync the Python environment with `uv`:

```bash
uv sync
```

Activate the virtual environment:

```bash
source .venv/bin/activate
```

Apply database migrations:

```bash
python manage.py migrate
```

Create a Django admin account:

```bash
python manage.py createsuperuser
```

Build the bundled knowledge base collection:

```bash
python scripts/build_knowledge_base.py --data-dir data/nuclear_energy --collection nuclear_energy_all
```

Run the Django development server on port `8005`:

```bash
python manage.py runserver 0.0.0.0:8005 --noreload
```

When the virtualenv is already activated, prefer:

```bash
python -m apps.matching.services.ai_agent
```

instead of `uv run python -m ...`.

## Useful Commands

Check the project configuration:

```bash
python manage.py check
```

Run the backend test suite:

```bash
python manage.py test api
```

Collect static files:

```bash
python manage.py collectstatic --noinput
```

## Django Admin

After creating a superuser, the admin UI is available at:

```text
http://localhost:8005/admin/
```

If deployed behind your public domain, use:

```text
https://dev.bridgeus.work/admin/
```

## Docker Run

1. Copy `.env.example` to `.env`
2. Fill in at least `DJANGO_SECRET_KEY`, `JWT_SIGNING_KEY`, and `ANTHROPIC_API_KEY`
3. Start the stack:

```bash
docker compose up --build
```

The Django API will be available at `http://localhost:8005`.

## Docker Notes

- Dialogue session state uses Redis when `REDIS_URL` is set.
- SQLite, Chroma, and Hugging Face cache data are persisted in the `app_data` Docker volume.
- The container entrypoint runs `migrate`, `collectstatic`, and then starts `gunicorn`.
- Admin static files are served by WhiteNoise in the backend container, so `/admin/` should render correctly without a separate nginx sidecar.
