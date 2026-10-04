"""Native CrewAI persistence and memory configuration for one job."""

from __future__ import annotations

import os
import re
from pathlib import Path

from orchestrator.agent.job import JobScope

_CHECKPOINT_EVENTS = [
    "task_completed",
    "agent_execution_completed",
    "agent_execution_error",
    "crew_kickoff_failed",
    "step_observation_completed",
    "tool_usage_error",
    "plan_replan_triggered",
]


def runtime_state_dir(scope: JobScope) -> Path | None:
    """Return an isolated persistent state directory for this job."""
    root = str(scope.workspace or "").strip()
    if not root:
        return None
    job_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(scope.job_id or "job"))
    return Path(root) / ".peon" / "crewai" / "jobs" / job_id


def latest_checkpoint(scope: JobScope) -> Path | None:
    """Return the newest native CrewAI checkpoint for this job."""
    root = runtime_state_dir(scope)
    if root is None:
        return None
    files = list((root / "checkpoints" / "main").glob("*.json"))
    return max(files, key=lambda path: path.stat().st_mtime_ns) if files else None


def checkpoint_config(scope: JobScope, *, resume: bool = False):
    """Build a native CrewAI checkpoint config, optionally restoring the latest."""
    root = runtime_state_dir(scope)
    if root is None:
        return None
    from crewai.state.checkpoint_config import CheckpointConfig

    location = root / "checkpoints"
    location.mkdir(parents=True, exist_ok=True)
    restore_from = latest_checkpoint(scope) if resume else None
    return CheckpointConfig(
        location=str(location),
        on_events=list(_CHECKPOINT_EVENTS),
        max_checkpoints=20,
        restore_from=str(restore_from) if restore_from else None,
    )


def build_memory(scope: JobScope):
    """Build project-scoped CrewAI memory with persistent local vector storage."""
    root = runtime_state_dir(scope)
    if root is None:
        return None
    from crewai.memory import Memory

    from orchestrator.config import get_config
    from orchestrator.crew.roles.factory import llm_id_for_crew

    cfg = get_config()
    provider = (
        os.environ.get("CREWAI_MEMORY_EMBEDDER_PROVIDER")
        or cfg.llm_provider
        or "openrouter"
    ).strip()
    embedder: dict = {"provider": provider, "config": {}}
    model = os.environ.get("CREWAI_MEMORY_EMBEDDER_MODEL", "").strip()
    if model:
        embedder["config"]["model"] = model
    scope_id = re.sub(
        r"[^a-zA-Z0-9_.-]+",
        "_",
        str(scope.project_id or scope.job_id or "default"),
    )
    memory_root = Path(scope.workspace) / ".peon" / "crewai" / "memory"
    memory_root.mkdir(parents=True, exist_ok=True)
    return Memory(
        llm=llm_id_for_crew(),
        storage=str(memory_root),
        embedder=embedder,
        root_scope=f"/peon/{scope_id}",
    )


def output_log_path(scope: JobScope) -> str | None:
    root = runtime_state_dir(scope)
    if root is None:
        return None
    root.mkdir(parents=True, exist_ok=True)
    return str(root / "execution.json")
