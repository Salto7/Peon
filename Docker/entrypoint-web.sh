#!/bin/sh
set -e
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-peon.config.settings}"
export PROJECT_WORKSPACES_DIR="${PROJECT_WORKSPACES_DIR:-/workspaces}"
mkdir -p "$PROJECT_WORKSPACES_DIR/platform"
# --fake-initial: tolerate an existing sqlite from earlier local schemas on the volume.
python manage.py migrate --noinput --fake-initial
exec "$@"
