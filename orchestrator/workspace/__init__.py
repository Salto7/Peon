"""Job workspace path helpers used by BasePlanner.persist."""

from __future__ import annotations

import re
from pathlib import Path

from orchestrator.config import get_config

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def workspaces_root() -> Path:
    root = Path(get_config().workspaces_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_workspace_key(key: str) -> str:
    raw = (key or "").strip().replace("/", "-").replace("\\", "-")
    cleaned = _SAFE.sub("-", raw).strip(".-") or "workspace"
    return cleaned[:64]


def job_dir(job_id: str) -> Path:
    root = workspaces_root()
    path = (root / safe_workspace_key(str(job_id))).resolve()
    path.relative_to(root)
    return path


def plans_dir(job_id: str) -> Path:
    return job_dir(job_id) / "plans"


def provision_job_workspace(job_id: str) -> Path:
    root = job_dir(job_id)
    for sub in ("plans", "inputs", "findings", "workspace"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root
