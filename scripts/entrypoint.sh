#!/bin/sh
set -e

# Ensure the sqlite directory exists (DATABASES NAME = BASE_DIR/data/db.sqlite3); it's
# git/docker-ignored, so a standalone container won't have it.
mkdir -p data

python manage.py migrate --noinput

# Collect static for production. In DEBUG, runserver serves static directly, so skip it.
case "${DEBUG:-}" in
    1 | true | True | TRUE | yes | on) : ;;
    *) python manage.py collectstatic --noinput ;;
esac

# Optional first-boot admin: set DJANGO_SUPERUSER_USERNAME + DJANGO_SUPERUSER_PASSWORD
# (and EMAIL) to create one automatically. No-op if that user already exists.
if [ -n "${DJANGO_SUPERUSER_USERNAME:-}" ] && [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
    python manage.py createsuperuser --noinput || true
fi

exec "$@"
