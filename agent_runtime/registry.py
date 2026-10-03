"""Runtime id → backend. Concrete backends register themselves from bootstrap."""

from __future__ import annotations

from agent_runtime.api import Runtime

_RUNTIMES: dict[str, Runtime] = {}


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
