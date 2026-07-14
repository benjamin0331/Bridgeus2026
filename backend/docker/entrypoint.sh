#!/bin/sh
set -eu

python manage.py migrate --noinput
python manage.py collectstatic --noinput

if [ "${PRELOAD_NLP_MODELS:-false}" = "true" ]; then
  python manage.py warm_nlp_models
fi

exec uvicorn take_a_bridge.asgi:application \
  --host 0.0.0.0 \
  --port "${PORT:-8005}" \
  --workers "${UVICORN_WORKERS:-1}" \
  --timeout-keep-alive "${UVICORN_TIMEOUT_KEEP_ALIVE:-180}"
