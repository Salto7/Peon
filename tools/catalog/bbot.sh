#!/usr/bin/env bash
set -euo pipefail
apt-get update
apt-get install -y --no-install-recommends pipx
pipx install bbot
bin_dir="$(pipx environment --value PIPX_BIN_DIR)"
ln -sf "$bin_dir/bbot" /usr/local/bin/bbot
