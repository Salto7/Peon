"""Logging setup for corp-OSINT (stderr; JSON stays on stdout)."""

from __future__ import annotations

import logging
import sys
from typing import Optional

LOG_NAME = "corp_osint"

# Verbosity → level
#   0 (default): INFO  — stage progress
#   1 (-v):      DEBUG — per-domain / HTTP detail
#   2 (-vv):     DEBUG + httpx-style extras (same DEBUG; more call sites)
#  -1 (-q):      WARNING — quiet
_LEVELS = {
    -1: logging.WARNING,
    0: logging.INFO,
    1: logging.DEBUG,
    2: logging.DEBUG,
}


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return package logger or a child (``corp_osint.<name>``)."""
    if name and not name.startswith(LOG_NAME):
        return logging.getLogger(f"{LOG_NAME}.{name}")
    return logging.getLogger(name or LOG_NAME)


def setup_logging(verbosity: int = 0, *, quiet: bool = False) -> logging.Logger:
    """
    Configure root ``corp_osint`` logger once.

    Messages go to stderr so stdout can stay pure JSON.
    """
    if quiet:
        verbosity = -1
    level = _LEVELS.get(max(-1, min(2, verbosity)), logging.INFO)

    log = logging.getLogger(LOG_NAME)
    log.setLevel(level)
    log.propagate = False

    # Replace handlers so repeated setup (tests / re-entry) is idempotent
    for h in list(log.handlers):
        log.removeHandler(h)

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    if level <= logging.DEBUG:
        fmt = logging.Formatter(
            "%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )
    else:
        fmt = logging.Formatter("%(levelname)-7s %(message)s")
    handler.setFormatter(fmt)
    log.addHandler(handler)

    # Soften noisy third-party libs unless -vv
    if verbosity < 2:
        for noisy in ("aiohttp", "asyncio", "urllib3", "playwright"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    log.debug("logging ready level=%s verbosity=%s", logging.getLevelName(level), verbosity)
    return log
