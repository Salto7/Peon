"""Django settings for automated tests — never touch production data/."""

from __future__ import annotations

from pathlib import Path

# Force isolation before importing production settings.
import os

_ROOT = Path(__file__).resolve().parent.parent.parent
_TEST_DATA = (_ROOT / ".pytest_data").resolve()
_TEST_DATA.mkdir(parents=True, exist_ok=True)
os.environ["PEON_DATA_DIR"] = str(_TEST_DATA)
os.environ.setdefault("SECRET_KEY", "peon-test-only-not-for-prod")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("SANDBOX_ENABLED", "false")

from peon.config.settings import *  # noqa: E402, F403

DATABASES["default"]["NAME"] = str(_TEST_DATA / "test.sqlite3")  # noqa: F405
PROJECT_WORKSPACES_DIR = (_TEST_DATA / "project_workspaces").resolve()  # noqa: F405
PROJECT_WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
STREAM_SOCKET_PATH = str(_TEST_DATA / "stream.sock")  # noqa: F405
DRAMATIQ_ENABLED = False
REDIS_URL = "redis://127.0.0.1:6379/15"
