#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# AdStrike v5.0 «AdStrike» — Launcher
# AUTHORISED PENETRATION TESTING ONLY
# ══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

RED='\033[91m'; GRN='\033[92m'; YLW='\033[93m'
CYN='\033[96m'; DIM='\033[2m';  RST='\033[0m'; BOLD='\033[1m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ADSTRIKE_VENV_DIR:-$SCRIPT_DIR/venv}"
MAIN="$SCRIPT_DIR/main.py"
OUTPUT_DIR="$SCRIPT_DIR/output"
LOG_FILE="$OUTPUT_DIR/session_$(date +%Y%m%d_%H%M%S).log"
BIN_DIR="$SCRIPT_DIR/tools/bin"

if [[ -d "$BIN_DIR" ]]; then
    export PATH="$BIN_DIR:$PATH"
fi

# Refuse sudo runs. User-installed tools commonly live in ~/.local/bin, and
# root-owned output files can block subsequent normal-user runs.
if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
    echo "  [-] Do not launch this with sudo. Run as your normal user."
    echo "      If output/ is currently root-owned from a previous sudo run, fix once with:"
    echo "          sudo chown -R \$(id -un):\$(id -gn) \"$OUTPUT_DIR\""
    exit 2
fi

ok()   { echo -e "  ${GRN}[+]${RST} $*"; }
warn() { echo -e "  ${YLW}[!]${RST} $*"; }
die()  { echo -e "  ${RED}[-]${RST} $* — aborting"; exit 1; }

mkdir -p "$OUTPUT_DIR"

# ── Preserve previous session logs ────────────────────────────────────────────
# Session logs are kept across runs so earlier engagements (e.g. a different
# target) stay available. Each run writes its own timestamped session_*.log; old
# ones are NOT deleted. Remove them manually if output/ grows too large.

# ── Python check ──────────────────────────────────────────────────────────────
command -v python3 &>/dev/null || die "python3 not found"
ok "Python $(python3 --version 2>&1 | awk '{print $2}')"

# ── venv ─────────────────────────────────────────────────────────────────────
if [[ ! -d "$VENV_DIR" ]]; then
    warn "${VENV_DIR#$SCRIPT_DIR/} not found — run: python -m venv venv && source venv/bin/activate && bash install.sh"
    exit 1
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
ok "Virtual environment activated"

# ── .env ─────────────────────────────────────────────────────────────────────
if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
    warn ".env not found — copying from template"
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    warn "Edit .env with your engagement details, then re-run"
    read -rp "  Press Enter to continue anyway (or Ctrl+C to abort)..."
else
    ok ".env loaded"
fi

# ── main.py check ─────────────────────────────────────────────────────────────
[[ -f "$MAIN" ]] || die "main.py not found at $MAIN"

# ── Launch ────────────────────────────────────────────────────────────────────
echo -e "\n  ${DIM}Log → $LOG_FILE${RST}\n"

cd "$SCRIPT_DIR"
# Keep ANSI colours on the terminal, but strip them from the saved log so the
# file is readable in editors. The sed filter first collapses carriage-return
# redraws (spinners/progress bars) to their final state, then removes all
# terminal escape sequences before appending to the log.
"$VENV_DIR/bin/python" main.py "$@" 2>&1 \
    | tee >(sed -u 's/.*\r//; s/\x1b\[[0-9;?]*[a-zA-Z]//g' >> "$LOG_FILE")
EXIT_CODE=${PIPESTATUS[0]}

echo
if (( EXIT_CODE == 0 )); then
    ok "Session ended cleanly — log saved → $LOG_FILE"
else
    warn "Exited with code $EXIT_CODE — log saved → $LOG_FILE"
fi
