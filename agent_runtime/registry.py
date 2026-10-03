"""Runtime id → backend."""

from __future__ import annotations

from agent_runtime.api import Runtime

_RUNTIMES: dict[str, Runtime] = {}
_BUILTIN_DONE = False


def register(runtime: Runtime) -> None:
    rid = (getattr(runtime, "id", "") or "").strip()
    if not rid:
        raise ValueError("runtime.id is required")
    _RUNTIMES[rid] = runtime


def get(runtime_id: str) -> Runtime:
    rid = (runtime_id or "").strip() or "sandbox"
    try:
        return _RUNTIMES[rid]
    except KeyError as exc:
        known = ", ".join(sorted(_RUNTIMES)) or "(none registered)"
        raise KeyError(f"Unknown agent runtime {rid!r}. Registered: {known}") from exc


def ids() -> list[str]:
    return sorted(_RUNTIMES)


def register_builtin() -> None:
    global _BUILTIN_DONE
    if _BUILTIN_DONE:
        return
    from agent_runtime.docker.runtime import DockerRuntime
    from agent_runtime.openshell.runtime import OpenShellRuntime

    register(DockerRuntime())
    register(OpenShellRuntime())
    _BUILTIN_DONE = True
