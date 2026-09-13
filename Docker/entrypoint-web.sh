#!/bin/sh
set -e
mkdir -p /data/project_workspaces
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-peon.config.settings}"
export PEON_DATA_DIR="${PEON_DATA_DIR:-/data}"
# --fake-initial: tolerate an existing sqlite from earlier local schemas on the volume.
python manage.py migrate --noinput --fake-initial
exec "$@"
