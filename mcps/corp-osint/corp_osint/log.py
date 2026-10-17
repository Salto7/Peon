"""Logging setup for corp-OSINT (all output via logger → stderr)."""

from __future__ import annotations

import logging
import sys

LOG_NAME = "corp_osint"

# Verbosity → level
#   0 (default): INFO  — stage progress
#   1 (-v):      DEBUG — per-domain / HTTP detail
#   2 (-vv):     DEBUG + extras
#  -1 (-q):      WARNING — quiet progress (JSON/plugin list still emitted)
_LEVELS = {
    -1: logging.WARNING,
    0: logging.INFO,
    1: logging.DEBUG,
    2: logging.DEBUG,
}


def get_logger(name: str | None = None) -> logging.Logger:
    """Return package logger or a child (``corp_osint.<name>``)."""
    if name and not name.startswith(LOG_NAME):
        return logging.getLogger(f"{LOG_NAME}.{name}")
    return logging.getLogger(name or LOG_NAME)


def emit(message: str) -> None:
    """Emit primary CLI payload (JSON / listings) via logger.

    Uses ``corp_osint.output``, which stays at INFO even when ``-q`` raises the
    progress logger to WARNING.
    """
    get_logger("output").info("%s", message)


def setup_logging(verbosity: int = 0, *, quiet: bool = False) -> logging.Logger:
    """
    Configure ``corp_osint`` loggers once.

    All messages go to stderr through the logging system (no ``print``).
    """
    if quiet:
        verbosity = -1
    level = _LEVELS.get(max(-1, min(2, verbosity)), logging.INFO)

    root = logging.getLogger(LOG_NAME)
    root.setLevel(logging.DEBUG)  # children decide; handlers filter
    root.propagate = False

    for h in list(root.handlers):
        root.removeHandler(h)

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
    root.addHandler(handler)

    # Payload channel: always visible at INFO (plugin list, result JSON)
    output = logging.getLogger(f"{LOG_NAME}.output")
    output.setLevel(logging.INFO)
    output.propagate = True  # uses root handler; raise handler floor for this path
    # Ensure quiet mode still shows payloads: root handler may be WARNING.
    # Attach a dedicated INFO handler if quiet suppressed INFO.
    for h in list(output.handlers):
        output.removeHandler(h)
    # Bare payload lines (no level prefix), independent of the progress level.
    out_handler = logging.StreamHandler(sys.stderr)
    out_handler.setLevel(logging.INFO)
    out_handler.setFormatter(logging.Formatter("%(message)s"))
    output.addHandler(out_handler)
    output.propagate = False

    if verbosity < 2:
        for noisy in ("aiohttp", "asyncio", "urllib3", "playwright"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    root.debug(
        "logging ready level=%s verbosity=%s",
        logging.getLevelName(level),
        verbosity,
    )
    return root
