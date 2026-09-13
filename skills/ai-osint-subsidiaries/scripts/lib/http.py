"""Skill-local re-exports of corp_osint_cli HTTP helpers."""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from corp_osint_cli.http import *  # noqa: E402,F401,F403
