#!/bin/sh
set -e

# Ensure the sqlite directory exists (DATABASES NAME = BASE_DIR/data/db.sqlite3); it's
# git/docker-ignored, so a standalone container won't have it.
mkdir -p data

python manage.py migrate --noinput
exec "$@"
