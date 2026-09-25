#!/bin/bash
# Compose worker: stream + RPC sockets + Dramatiq (1 process / N threads).
set -euo pipefail
python manage.py run_stream_server &
STREAM_PID=$!
python manage.py run_rpc_server &
RPC_PID=$!
cleanup() {
  kill "$STREAM_PID" "$RPC_PID" 2>/dev/null || true
}
trap cleanup EXIT

# Prefer DB/UI override; fall back to DRAMATIQ_THREADS env / default 4.
THREADS="$(
  python - <<'PY' 2>/dev/null || true
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "peon.config.settings")
import django
django.setup()
from peon.projects.runtime_settings import PeonSettings
print(int(PeonSettings.get_int("DRAMATIQ_THREADS", 4)))
PY
)"
THREADS="${THREADS:-${DRAMATIQ_THREADS:-4}}"
case "$THREADS" in
  ''|*[!0-9]*) THREADS=4 ;;
esac
if [ "$THREADS" -lt 1 ]; then THREADS=1; fi

exec dramatiq peon.projects.tasks --processes 1 --threads "$THREADS" -Q peon.jobs
