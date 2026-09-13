"""Operator-tunable runtime policy (DB row + .env defaults).

Live knobs apply on next claim / agent start. ``DRAMATIQ_THREADS`` is read by
the worker entrypoint at process start — changing it requires a worker restart.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

# Keys that only take effect after restarting the Dramatiq worker process.
RESTART_REQUIRED = frozenset({"DRAMATIQ_THREADS"})

# (key, django settings attr, coerce, min, max, default-if-missing)
_SPECS: dict[str, tuple[str, str, int, int, int]] = {
    "MAX_PARALLEL_PROJECTS": ("MAX_PARALLEL_PROJECTS", "int", 1, 32, 3),
    "MAX_AGENTS_PER_PROJECT": ("MAX_AGENTS_PER_PROJECT", "int", 1, 32, 2),
    "DRAMATIQ_THREADS": ("DRAMATIQ_THREADS", "int", 1, 64, 4),
    "AGENT_MAX_FAILURE_REPLANS": ("AGENT_MAX_FAILURE_REPLANS", "int", 0, 20, 2),
    "AGENT_MAX_ITERATIONS": ("AGENT_MAX_ITERATIONS", "int", 1, 500, 40),
    "AGENT_MAX_SUBAGENTS": ("AGENT_MAX_SUBAGENTS", "int", 0, 32, 4),
    "AGENT_MAX_SUBAGENT_DEPTH": ("AGENT_MAX_SUBAGENT_DEPTH", "int", 1, 8, 2),
    "AGENT_RUNTIME_ENABLED": ("AGENT_RUNTIME_ENABLED", "bool", 0, 1, 1),
}


def _env_default(key: str) -> Any:
    attr, kind, lo, hi, fallback = _SPECS[key]
    raw = getattr(settings, attr, None)
    if kind == "bool":
        if raw is None:
            return bool(fallback)
        return bool(raw)
    try:
        val = int(raw) if raw is not None else int(fallback)
    except (TypeError, ValueError):
        val = int(fallback)
    return max(lo, min(hi, val))


class PeonSettings:
    """Read/write singleton RuntimeSettings with env-backed defaults."""

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        return {k: _env_default(k) for k in _SPECS}

    @classmethod
    def row(cls):
        from peon.projects.models import RuntimeSettings

        obj, _ = RuntimeSettings.objects.get_or_create(
            pk=1, defaults={"values": cls.defaults()}
        )
        if not isinstance(obj.values, dict):
            obj.values = {}
            obj.save(update_fields=["values", "updated_at"])
        return obj

    @classmethod
    def as_dict(cls) -> dict[str, Any]:
        base = cls.defaults()
        try:
            stored = cls.row().values or {}
        except Exception:
            return base
        out = dict(base)
        for key in _SPECS:
            if key in stored and stored[key] is not None:
                out[key] = cls._coerce(key, stored[key])
        return out

    @classmethod
    def get(cls, key: str) -> Any:
        return cls.as_dict().get(key, _env_default(key) if key in _SPECS else None)

    @classmethod
    def get_int(cls, key: str, default: int = 0) -> int:
        try:
            return int(cls.get(key))
        except (TypeError, ValueError):
            return int(default)

    @classmethod
    def get_bool(cls, key: str, default: bool = True) -> bool:
        val = cls.get(key)
        if isinstance(val, bool):
            return val
        if val is None:
            return default
        return str(val).strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def _coerce(cls, key: str, raw: Any) -> Any:
        _, kind, lo, hi, fallback = _SPECS[key]
        if kind == "bool":
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in {"1", "true", "yes", "on"}
        try:
            val = int(raw)
        except (TypeError, ValueError):
            val = int(fallback)
        return max(lo, min(hi, val))

    @classmethod
    def update(cls, updates: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Merge updates; return (new values, restart-required keys that changed)."""
        row = cls.row()
        current = cls.as_dict()
        merged = dict(current)
        restart_changed: list[str] = []
        for key, raw in (updates or {}).items():
            if key not in _SPECS:
                continue
            new = cls._coerce(key, raw)
            if key in RESTART_REQUIRED and current.get(key) != new:
                restart_changed.append(key)
            merged[key] = new
        row.values = {k: merged[k] for k in _SPECS}
        row.save(update_fields=["values", "updated_at"])
        return merged, restart_changed

    @classmethod
    def field_meta(cls) -> list[dict[str, Any]]:
        """UI metadata for the settings form."""
        labels = {
            "MAX_PARALLEL_PROJECTS": (
                "Max parallel projects",
                "Distinct projects that may have RUNNING jobs at once.",
                False,
            ),
            "MAX_AGENTS_PER_PROJECT": (
                "Max agents per project",
                "RUNNING jobs (root + subagents) allowed for one project.",
                False,
            ),
            "DRAMATIQ_THREADS": (
                "Dramatiq worker threads",
                "Thread pool size for the worker process. Requires restarting the worker service.",
                True,
            ),
            "AGENT_MAX_FAILURE_REPLANS": (
                "Agent failure replans",
                "Replan/recover passes after failure streaks within one Job.",
                False,
            ),
            "AGENT_MAX_ITERATIONS": (
                "Agent max iterations",
                "Hard cap on act↔tool iterations per Job.",
                False,
            ),
            "AGENT_MAX_SUBAGENTS": (
                "Agent max subagents",
                "Max concurrent child Jobs via spawn_subagent.",
                False,
            ),
            "AGENT_MAX_SUBAGENT_DEPTH": (
                "Agent max subagent depth",
                "Max Job parent→child depth (2 = root + one level).",
                False,
            ),
            "AGENT_RUNTIME_ENABLED": (
                "Agent runtime enabled",
                "Emergency kill switch for LangGraph job agents.",
                False,
            ),
        }
        values = cls.as_dict()
        rows: list[dict[str, Any]] = []
        for key, (attr, kind, lo, hi, _fb) in _SPECS.items():
            label, help_text, restart = labels[key]
            rows.append(
                {
                    "key": key,
                    "label": label,
                    "help": help_text,
                    "restart_required": restart,
                    "kind": kind,
                    "min": lo,
                    "max": hi,
                    "value": values[key],
                    "env_default": _env_default(key),
                }
            )
        return rows
