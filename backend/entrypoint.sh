#!/bin/sh
set -e

mkdir -p /app/backend/logs /app/backend/db

python manage.py migrate --noinput
python cli/create_init_user.py

if [ "${BILLING_ENABLED:-false}" = "true" ]; then
  python manage.py seed_billing
fi

exec gunicorn app.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers "${WEB_CONCURRENCY:-2}" \
  --threads "${WEB_THREADS:-2}" \
  --timeout "${WEB_TIMEOUT:-180}" \
  --access-logfile - \
  --error-logfile -
