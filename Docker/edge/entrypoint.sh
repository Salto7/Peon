#!/bin/sh
# Minimal TLS edge for Peon.
set -eu

CERT_DIR="${SSL_CERT_DIR:-/certs}"
CERT_FILE="${SSL_CERT_FILE:-$CERT_DIR/cert.pem}"
KEY_FILE="${SSL_KEY_FILE:-$CERT_DIR/key.pem}"
HOST="${PUBLIC_HOST:-localhost}"

mkdir -p "$CERT_DIR"

STAMP="$CERT_DIR/.san_stamp"
SAN="DNS:${HOST},DNS:localhost,DNS:edge,DNS:web,IP:127.0.0.1"
WANTED="${HOST}"

if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ] || [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$WANTED" ]; then
  echo "Generating self-signed TLS cert (SAN=${SAN})..."
  openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
    -keyout "$KEY_FILE" \
    -out "$CERT_FILE" \
    -subj "/CN=${HOST}" \
    -addext "subjectAltName=${SAN}"
  chmod 600 "$KEY_FILE"
  printf '%s' "$WANTED" > "$STAMP"
  echo "Wrote $CERT_FILE and $KEY_FILE"
else
  echo "Using existing TLS cert: $CERT_FILE"
fi

export SSL_CERT_FILE="$CERT_FILE"
export SSL_KEY_FILE="$KEY_FILE"

envsubst '${SSL_CERT_FILE} ${SSL_KEY_FILE}' \
  < /etc/caddy/Caddyfile.template > /etc/caddy/Caddyfile

echo "Starting edge on :443 (host=${HOST} → web:8000)"
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
