#!/bin/sh
set -eu

python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec gunicorn BridgeUs_Django.wsgi:application \
  --bind 0.0.0.0:"${PORT:-8005}" \
  --workers "${GUNICORN_WORKERS:-2}" \
  --timeout "${GUNICORN_TIMEOUT:-180}"
