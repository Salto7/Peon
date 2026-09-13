#!/usr/bin/env bash
# Start the Peon Compose stack (web + edge + redis + worker).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Starting Peon"
[[ -f .env ]] || cp .env.example .env
docker compose up -d --build

echo "==> Health (HTTPS via edge; -k for self-signed)"
sleep 3
PORT="${WEB_PORT:-8000}"
curl -k -fsS "https://127.0.0.1:${PORT}/health/" || true
echo
docker compose ps
echo "Done. UI https://127.0.0.1:${PORT}/  (accept self-signed cert)"
echo "Worker: docker compose logs -f worker"
