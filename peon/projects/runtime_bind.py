"""Bind an agent_runtime session into the process JobEnv / Session context."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from django.conf import settings

from agent_runtime.api import Session


def runtime_state_dir() -> Path:
    """Shared Docker/OpenShell state dir (sibling of project workspaces)."""
    root = Path(
        getattr(settings, "PROJECT_WORKSPACES_DIR", Path.cwd() / "data")
    ).resolve()
    return root.parent / "runtime"


def bind_runtime_session(session: Any, *, default_workdir: str = "/workspace") -> None:
    """Set workdir env, JobEnv lookup, and ``Session.bind``."""
    from orchestrator.utils.job_env import JobEnv

    work = (session.info.workdir or default_workdir).strip() or default_workdir
    os.environ["ORCHESTRATOR_SANDBOX_WORKDIR"] = work
    if JobEnv.current():
        JobEnv.bind({**JobEnv.current(), "ORCHESTRATOR_SANDBOX_WORKDIR": work})
    else:
        JobEnv.bind({"ORCHESTRATOR_SANDBOX_WORKDIR": work})
    session.set_env_lookup(JobEnv.get)
    Session.bind(session)
