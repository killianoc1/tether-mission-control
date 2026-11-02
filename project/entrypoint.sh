#!/bin/sh
# Container start-up: migrate, collect static files, optional first-run bootstrap,
# then hand over to the CMD (Django runserver).
set -e

echo "[entrypoint] applying migrations"
python manage.py migrate --noinput

echo "[entrypoint] collecting static files"
python manage.py collectstatic --noinput >/dev/null

# Create the demo account (idempotent) and, if the DB is empty, load demo data.
python manage.py bootstrap_demo

exec "$@"
