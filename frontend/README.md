# BridgeUs Frontend

This repository is the Vite + React frontend for BridgeUs.

The frontend expects a separate backend that provides these endpoints:

- `POST /api/token/`
- `POST /api/dialogue/sessions/`
- `POST /api/dialogue/sessions/:id/reply/`

## Local Development

Install dependencies and run the Vite dev server:

```bash
npm install
npm run dev
```

In development, Vite proxies `/api/*` to `http://127.0.0.1:8005` unless `VITE_PROXY_TARGET` is set.

## Docker Test Deployment

This repo includes a lightweight Docker setup intended for testing:

- The frontend is built into static files.
- `nginx` serves the frontend.
- `nginx` forwards `/api/*` to your Django project running on the same host.

### Assumptions

- Your Django project is already running on the host machine.
- Django is reachable at `host.docker.internal:8005` from Docker.
- For local testing, the simplest command is usually:

```bash
python manage.py runserver 0.0.0.0:8005
```

### Start the frontend container

```bash
docker compose up --build
```

Then open:

```text
http://localhost:8080
```

### Backend target override

The compose file forwards `/api/*` to these defaults:

- `BACKEND_HOST=host.docker.internal`
- `BACKEND_PORT=8005`

You can override them at startup:

```bash
BACKEND_HOST=host.docker.internal BACKEND_PORT=8005 docker compose up --build
```

### Django-side notes

If the frontend opens but API calls fail, check these first:

- Django is actually listening on `0.0.0.0:8005`, not only on an isolated local interface.
- `ALLOWED_HOSTS` includes the host you use in the browser, usually `localhost` and `127.0.0.1`.
- If you use CSRF protection on API endpoints, make sure your proxy setup and trusted origins match your Django settings.

### Linux note

On Docker Desktop for macOS, `host.docker.internal` usually works out of the box.

If you later run this on Linux, you may need to add this to the frontend service in `docker-compose.yml`:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```
