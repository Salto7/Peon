"""Pytest harness for characterization tests.

No network and no real LLM. Workspace directories point at ``tmp_path``.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_BOOT = Path(tempfile.mkdtemp(prefix="peon-pytest-boot-"))
_BOOT_WORK = _BOOT / "workspaces"
(_BOOT_WORK / "platform").mkdir(parents=True)

os.environ["DJANGO_SETTINGS_MODULE"] = "peon.config.settings"
os.environ["DRAMATIQ_ENABLED"] = "false"
os.environ["DEBUG"] = "true"
os.environ["SECRET_KEY"] = "peon-pytest-secret"
os.environ["PROJECT_WORKSPACES_DIR"] = str(_BOOT_WORK)
os.environ["LLM_PROXY_ENABLED"] = "false"
os.environ["COGNEE_ENABLED"] = "false"
os.environ["PEON_DREAMING_ENABLED"] = "false"
os.environ["PEON_MEMORY_ENABLED"] = "false"
os.environ["DURABLE_SCHEDULER_ENABLED"] = "false"
for _key in (
    "LITELLM_MODEL",
    "LLM_MODEL",
    "LITELLM_API_KEY",
    "LITELLM_API_BASE",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "LLM_API_KEY",
    "LLM_PROVIDER",
):
    os.environ.pop(_key, None)

# AppConfig.ready treats any program name other than manage.py as a web
# process and starts proxy/scheduler threads. Characterization tests are
# one-shot, same as ``python -c``.
_ARGV = sys.argv
sys.argv = ["-c"]
try:
    import django

    django.setup()
finally:
    sys.argv = _ARGV

import pytest


@pytest.fixture(autouse=True)
def workspace_dirs(tmp_path, monkeypatch):
    """Point Django and orchestrator workspace dirs at this test's tmp_path."""
    root = tmp_path / "workspaces"
    platform = root / "platform"
    platform.mkdir(parents=True)
    from django.conf import settings

    monkeypatch.setattr(settings, "PROJECT_WORKSPACES_DIR", root)
    monkeypatch.setattr(settings, "PLATFORM_WORKSPACE_DIR", platform)
    from orchestrator.config import configure, get_config

    configure(
        get_config().merge(
            workspaces_dir=root,
            platform_workspace_dir=platform,
        )
    )
    return root
