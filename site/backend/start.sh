#!/bin/sh
# Railway: PORT подставляется платформой; fallback для локального запуска.
export PORT="${PORT:-8000}"
export PYTHONUNBUFFERED=1
set -e
echo "[start] migrate..."
python manage.py migrate --noinput
echo "[start] gunicorn on 0.0.0.0:${PORT}..."
exec gunicorn config.wsgi:application --bind "0.0.0.0:${PORT}" --log-level info
