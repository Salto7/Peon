"""Resolve agent / crew modules from RuntimeConfig (parallel to llm.registry)."""

from __future__ import annotations

from orchestrator.agent.runtime_base import AgentRuntimeBase
from orchestrator.config import get_config
from orchestrator.crew.runtime_base import CrewRuntimeBase

# Job-level modules (one Job / role tool loop).
_JOB_MODULES: dict[str, str] = {
    "crewai": "orchestrator.crew.runtimes.job_crewai:CrewAIJobRuntime",
}

# Project-level modules (hierarchical crew).
_CREW_MODULES: dict[str, str] = {
    "crewai": "orchestrator.crew.runtimes.project_crewai:ProjectCrewRuntime",
    "noop": "orchestrator.crew.runtimes.noop:NoopCrewRuntime",
}

_job_cached: AgentRuntimeBase | None = None
_job_cached_id: str | None = None
_crew_cached: CrewRuntimeBase | None = None
_crew_cached_id: str | None = None


def reset_agent_modules() -> None:
    """Drop cached runtimes (call after ``configure()``)."""
    global _job_cached, _job_cached_id, _crew_cached, _crew_cached_id
    _job_cached = None
    _job_cached_id = None
    _crew_cached = None
    _crew_cached_id = None


def _load_class(path: str) -> type:
    module_path, _, cls_name = path.partition(":")
    if not module_path or not cls_name:
        raise RuntimeError(f"invalid module path: {path!r}")
    import importlib

    mod = importlib.import_module(module_path)
    cls = getattr(mod, cls_name, None)
    if cls is None:
        raise RuntimeError(f"module {module_path!r} has no {cls_name}")
    return cls


def agent_module_id() -> str:
    """Return configured module id (crewai). Legacy ``langgraph`` maps to crewai."""
    raw = (get_config().agent_module or "crewai").strip().lower() or "crewai"
    return "crewai" if raw == "langgraph" else raw


def get_job_runtime() -> AgentRuntimeBase:
    """Return the Job-level agent runtime for ``AGENT_MODULE``."""
    global _job_cached, _job_cached_id
    module_id = agent_module_id()
    if _job_cached is not None and _job_cached_id == module_id:
        return _job_cached
    path = _JOB_MODULES.get(module_id)
    if path is None:
        known = ", ".join(sorted(_JOB_MODULES))
        raise RuntimeError(
            f"Unknown AGENT_MODULE={module_id!r}; known modules: {known}"
        )
    _job_cached = _load_class(path)()
    _job_cached_id = module_id
    return _job_cached


def get_crew_runtime() -> CrewRuntimeBase:
    """Return the project-level crew runtime."""
    global _crew_cached, _crew_cached_id
    module_id = agent_module_id()
    crew_id = module_id if module_id in _CREW_MODULES else "noop"
    if _crew_cached is not None and _crew_cached_id == crew_id:
        return _crew_cached
    path = _CREW_MODULES[crew_id]
    _crew_cached = _load_class(path)()
    _crew_cached_id = crew_id
    return _crew_cached
