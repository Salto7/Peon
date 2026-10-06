"""Operator-tunable runtime policy (DB row + .env defaults).

Live knobs apply on next claim / agent start. ``DRAMATIQ_THREADS`` is read by
the worker entrypoint at process start — changing it requires a worker restart.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from peon.projects.llm_proxy import LlmProxy
from peon.projects.models import RuntimeSettings

# Keys that only take effect after restarting the Dramatiq worker process.
RESTART_REQUIRED = frozenset({"DRAMATIQ_THREADS"})

# UI categories (order = display order).
CATEGORIES: list[tuple[str, str]] = [
    ("queue", "Queue & concurrency"),
    ("agent", "Agent governors"),
    ("crew", "CrewAI reasoning"),
    ("integrations", "Integrations"),
]

# Spec: settings_attr, kind (int|bool|choice), lo|choices, hi, default, category
_SPECS: dict[str, tuple[str, str, Any, Any, Any, str]] = {
    "MAX_PARALLEL_PROJECTS": ("MAX_PARALLEL_PROJECTS", "int", 1, 32, 3, "queue"),
    "MAX_AGENTS_PER_PROJECT": ("MAX_AGENTS_PER_PROJECT", "int", 1, 32, 2, "queue"),
    "DRAMATIQ_THREADS": ("DRAMATIQ_THREADS", "int", 1, 64, 4, "queue"),
    "JOB_STUCK_RUNNING_SECONDS": (
        "JOB_STUCK_RUNNING_SECONDS",
        "int",
        60,
        86400,
        7200,
        "queue",
    ),
    "AGENT_MAX_FAILURE_REPLANS": (
        "AGENT_MAX_FAILURE_REPLANS",
        "int",
        0,
        20,
        2,
        "agent",
    ),
    "AGENT_MAX_ITERATIONS": ("AGENT_MAX_ITERATIONS", "int", 1, 500, 40, "agent"),
    "AGENT_MAX_SUBAGENTS": ("AGENT_MAX_SUBAGENTS", "int", 0, 32, 4, "agent"),
    "AGENT_MAX_SUBAGENT_DEPTH": (
        "AGENT_MAX_SUBAGENT_DEPTH",
        "int",
        1,
        8,
        2,
        "agent",
    ),
    "AGENT_RUNTIME_ENABLED": ("AGENT_RUNTIME_ENABLED", "bool", 0, 1, 1, "agent"),
    "CREW_REASONING_EFFORT": (
        "CREW_REASONING_EFFORT",
        "choice",
        ("low", "medium", "high"),
        (),
        "low",
        "crew",
    ),
    "CREW_REASONING_MAX_ATTEMPTS": (
        "CREW_REASONING_MAX_ATTEMPTS",
        "int",
        1,
        5,
        1,
        "crew",
    ),
    "LLM_PROXY_ENABLED": ("LLM_PROXY_ENABLED", "bool", 0, 1, 0, "integrations"),
}

_LABELS: dict[str, tuple[str, str]] = {
    "MAX_PARALLEL_PROJECTS": (
        "Max parallel projects",
        "Distinct projects that may have RUNNING jobs at once.",
    ),
    "MAX_AGENTS_PER_PROJECT": (
        "Max agents per project",
        "RUNNING jobs (root + subagents) allowed for one project.",
    ),
    "DRAMATIQ_THREADS": (
        "Dramatiq worker threads",
        "Thread pool size for the worker process. Requires restarting the worker service.",
    ),
    "JOB_STUCK_RUNNING_SECONDS": (
        "Stuck job reclaim (seconds)",
        "RUNNING jobs idle longer than this are failed so the queue can continue.",
    ),
    "AGENT_MAX_FAILURE_REPLANS": (
        "Agent failure replans",
        "PM recovery replans allowed after failed specialist objective jobs.",
    ),
    "AGENT_MAX_ITERATIONS": (
        "Agent max iterations",
        "Hard cap on CrewAI max_iter per Job (roles may set a lower value).",
    ),
    "AGENT_MAX_SUBAGENTS": (
        "Agent max subagents",
        "Max concurrent child Jobs via spawn_agent.",
    ),
    "AGENT_MAX_SUBAGENT_DEPTH": (
        "Agent max subagent depth",
        "Max Job parent→child depth (2 = root + one level).",
    ),
    "AGENT_RUNTIME_ENABLED": (
        "Agent runtime enabled",
        "Emergency kill switch for CrewAI job / project agents.",
    ),
    "CREW_REASONING_EFFORT": (
        "CrewAI reasoning effort",
        "When a ROLE.yaml sets reasoning: true — PlanningConfig effort (low/medium/high).",
    ),
    "CREW_REASONING_MAX_ATTEMPTS": (
        "CrewAI reasoning max attempts",
        "When ROLE.yaml sets reasoning: true — max planning refine attempts.",
    ),
    "LLM_PROXY_ENABLED": (
        "LiteLLM proxy enabled",
        "Start the OpenAI-compatible /v1 gateway for OpenCode (Learn Toolsmith).",
    ),
}


def _env_default(key: str) -> Any:
    attr, kind, lo, hi, fallback, _cat = _SPECS[key]
    raw = getattr(settings, attr, None)
    if kind == "bool":
        if raw is None:
            return bool(fallback)
        return bool(raw)
    if kind == "choice":
        choices = tuple(lo) if isinstance(lo, (tuple, list)) else ()
        val = str(raw if raw is not None else fallback).strip().lower()
        return val if val in choices else str(fallback)
    try:
        val = int(raw) if raw is not None else int(fallback)
    except (TypeError, ValueError):
        val = int(fallback)
    return max(int(lo), min(int(hi), val))


class PeonSettings:
    """Read/write singleton RuntimeSettings with env-backed defaults."""

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        return {k: _env_default(k) for k in _SPECS}

    @classmethod
    def row(cls):
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
    def get_str(cls, key: str, default: str = "") -> str:
        val = cls.get(key)
        if val is None:
            return default
        return str(val).strip()

    @classmethod
    def _coerce(cls, key: str, raw: Any) -> Any:
        _, kind, lo, hi, fallback, _cat = _SPECS[key]
        if kind == "bool":
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in {"1", "true", "yes", "on"}
        if kind == "choice":
            choices = tuple(lo) if isinstance(lo, (tuple, list)) else ()
            val = str(raw).strip().lower()
            return val if val in choices else str(fallback)
        try:
            val = int(raw)
        except (TypeError, ValueError):
            val = int(fallback)
        return max(int(lo), min(int(hi), val))

    @classmethod
    def update(cls, updates: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Merge updates; return (new values, restart-required keys that changed)."""
        row = cls.row()
        current = cls.as_dict()
        merged = dict(current)
        restart_changed: list[str] = []
        proxy_changed = False
        for key, raw in (updates or {}).items():
            if key not in _SPECS:
                continue
            new = cls._coerce(key, raw)
            if key in RESTART_REQUIRED and current.get(key) != new:
                restart_changed.append(key)
            if key == "LLM_PROXY_ENABLED" and current.get(key) != new:
                proxy_changed = True
            merged[key] = new
        row.values = {k: merged[k] for k in _SPECS}
        row.save(update_fields=["values", "updated_at"])
        if proxy_changed:
            try:
                LlmProxy.shared().apply(bool(merged.get("LLM_PROXY_ENABLED")))
            except Exception:
                pass
        return merged, restart_changed

    @classmethod
    def field_meta(cls) -> list[dict[str, Any]]:
        """Flat UI metadata for the settings form."""
        values = cls.as_dict()
        rows: list[dict[str, Any]] = []
        for key, (attr, kind, lo, hi, _fb, category) in _SPECS.items():
            label, help_text = _LABELS[key]
            row: dict[str, Any] = {
                "key": key,
                "label": label,
                "help": help_text,
                "restart_required": key in RESTART_REQUIRED,
                "kind": kind,
                "category": category,
                "value": values[key],
                "env_default": _env_default(key),
            }
            if kind == "choice":
                row["choices"] = list(lo)
                row["min"] = ""
                row["max"] = ""
            elif kind == "bool":
                row["min"] = 0
                row["max"] = 1
            else:
                row["min"] = lo
                row["max"] = hi
            rows.append(row)
        return rows

    @classmethod
    def categorized_fields(cls) -> list[dict[str, Any]]:
        """Fields grouped for the settings page."""
        by_cat: dict[str, list[dict[str, Any]]] = {cid: [] for cid, _ in CATEGORIES}
        for field in cls.field_meta():
            by_cat.setdefault(field["category"], []).append(field)
        out: list[dict[str, Any]] = []
        for cid, label in CATEGORIES:
            fields = by_cat.get(cid) or []
            if fields:
                out.append({"id": cid, "label": label, "fields": fields})
        return out
