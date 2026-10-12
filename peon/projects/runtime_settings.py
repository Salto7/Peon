"""Operator-tunable runtime policy (DB row + .env defaults).

``.env`` / Django settings supply defaults. Peon Settings (DB) overrides them.
Keys in ``RESTART_REQUIRED`` need a Peon restart (web and/or worker) after save.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from peon.projects.llm_proxy import LlmProxy
from peon.projects.models import RuntimeSettings

# Keys that only take effect after restarting Peon (web and/or worker / compose).
RESTART_REQUIRED = frozenset(
    {
        "DRAMATIQ_MAX_THREADS",
        "DRAMATIQ_ENABLED",
        "DRAMATIQ_TIME_LIMIT_MS",
        "DRAMATIQ_MAX_RETRIES",
        "REDIS_URL",
        "DEBUG",
        "ALLOWED_HOSTS",
        "PUBLIC_URL",
        "CSRF_TRUSTED_ORIGINS",
        "PROJECT_WORKSPACES_DIR",
        "SKILLS_DIR",
        "SKILLS_IMPORTED_DIR",
        "HELPERS_DIR",
        "TOOLS_CATALOG_DIR",
        "SANDBOX_IMAGE",
        "OPENSHELL_IMAGE",
        "LEARN_LAB_IMAGE",
        "PROJECT_SANDBOX_PREFIX",
        "LLM_PROVIDER",
        "LLM_MODULE",
        "LITELLM_MODEL",
        "LLM_TEMPERATURE",
        "LLM_PROXY_URL",
        "LLM_PROXY_PORT",
        "LLM_PROXY_CONTAINER",
        "LLM_PROXY_NETWORK",
        "LITELLM_MASTER_KEY",
        "OPENCODE_MODEL",
        "COGNEE_PORT",
        "COGNEE_EMBEDDING_MODEL",
        "COGNEE_EMBEDDING_DIMENSIONS",
        "DURABLE_SCHEDULER_INTERVAL_MS",
        "AGENT_MODULE",
        "WEB_PORT",
        "PUBLIC_HOST",
    }
)

# UI categories (order = display order).
CATEGORIES: list[tuple[str, str]] = [
    ("queue", "Queue & concurrency"),
    ("agent", "Agent governors"),
    ("crew", "CrewAI reasoning"),
    ("memory", "Memory & dreaming"),
    ("llm", "LLM & proxy"),
    ("sandbox", "Sandbox & images"),
    ("paths", "Paths"),
    ("system", "System"),
    ("integrations", "Integrations"),
]

# Spec: settings_attr, kind (int|bool|choice|str|float), lo|choices, hi, default, category
# For str: lo/hi unused. Defaults are fallbacks when Django setting attr missing.
_SPECS: dict[str, tuple[str, str, Any, Any, Any, str]] = {
    # Queue
    "MAX_PARALLEL_PROJECTS": ("MAX_PARALLEL_PROJECTS", "int", 1, 32, 3, "queue"),
    "MAX_AGENTS_PER_PROJECT": ("MAX_AGENTS_PER_PROJECT", "int", 1, 64, 8, "queue"),
    "DRAMATIQ_MAX_THREADS": ("DRAMATIQ_MAX_THREADS", "int", 1, 64, 8, "queue"),
    "DRAMATIQ_ENABLED": ("DRAMATIQ_ENABLED", "bool", 0, 1, 1, "queue"),
    "DRAMATIQ_TIME_LIMIT_MS": (
        "DRAMATIQ_TIME_LIMIT_MS",
        "int",
        60_000,
        86_400_000,
        21_600_000,
        "queue",
    ),
    "DRAMATIQ_MAX_RETRIES": ("DRAMATIQ_MAX_RETRIES", "int", 0, 20, 3, "queue"),
    "JOB_STUCK_RUNNING_SECONDS": (
        "JOB_STUCK_RUNNING_SECONDS",
        "int",
        60,
        86400,
        7200,
        "queue",
    ),
    "REDIS_URL": ("REDIS_URL", "str", "", "", "redis://redis:6379/0", "queue"),
    "DURABLE_SCHEDULER_ENABLED": (
        "DURABLE_SCHEDULER_ENABLED",
        "bool",
        0,
        1,
        1,
        "queue",
    ),
    "DURABLE_SCHEDULER_INTERVAL_MS": (
        "DURABLE_SCHEDULER_INTERVAL_MS",
        "int",
        15_000,
        3_600_000,
        60_000,
        "queue",
    ),
    # Agent
    "AGENT_MODULE": ("AGENT_MODULE", "choice", ("crewai",), (), "crewai", "agent"),
    "AGENT_MAX_FAILURE_REPLANS": (
        "AGENT_MAX_FAILURE_REPLANS",
        "int",
        0,
        20,
        2,
        "agent",
    ),
    "AGENT_MAX_ITERATIONS": ("AGENT_MAX_ITERATIONS", "int", 1, 500, 40, "agent"),
    "AGENT_MAX_SUBAGENTS": ("AGENT_MAX_SUBAGENTS", "int", 0, 32, 8, "agent"),
    "AGENT_MAX_SUBAGENT_DEPTH": (
        "AGENT_MAX_SUBAGENT_DEPTH",
        "int",
        1,
        8,
        2,
        "agent",
    ),
    "AGENT_RUNTIME_ENABLED": ("AGENT_RUNTIME_ENABLED", "bool", 0, 1, 1, "agent"),
    # Crew
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
    # Memory
    "COGNEE_ENABLED": ("COGNEE_ENABLED", "bool", 0, 1, 1, "memory"),
    "PEON_MEMORY_ENABLED": ("PEON_MEMORY_ENABLED", "bool", 0, 1, 1, "memory"),
    "PEON_DREAMING_ENABLED": ("PEON_DREAMING_ENABLED", "bool", 0, 1, 1, "memory"),
    "PEON_DREAMING_INTERVAL_S": (
        "PEON_DREAMING_INTERVAL_S",
        "int",
        60,
        86_400,
        900,
        "memory",
    ),
    "COGNEE_PORT": ("COGNEE_PORT", "int", 1, 65535, 8001, "memory"),
    "COGNEE_HTTP_TIMEOUT_S": ("COGNEE_HTTP_TIMEOUT_S", "float", 5, 600, 120, "memory"),
    "COGNEE_EMBEDDING_MODEL": (
        "COGNEE_EMBEDDING_MODEL",
        "str",
        "",
        "",
        "openrouter/openai/text-embedding-3-small",
        "memory",
    ),
    "COGNEE_EMBEDDING_DIMENSIONS": (
        "COGNEE_EMBEDDING_DIMENSIONS",
        "int",
        64,
        8192,
        1536,
        "memory",
    ),
    # LLM
    "LLM_PROVIDER": ("LLM_PROVIDER", "str", "", "", "openrouter", "llm"),
    "LLM_MODULE": ("LLM_MODULE", "str", "", "", "litellm", "llm"),
    "LITELLM_MODEL": (
        "LITELLM_MODEL",
        "str",
        "",
        "",
        "openrouter/openai/gpt-5.6-luna",
        "llm",
    ),
    "LLM_TEMPERATURE": ("LLM_TEMPERATURE", "float", 0, 2, 0, "llm"),
    "LLM_PROXY_ENABLED": ("LLM_PROXY_ENABLED", "bool", 0, 1, 1, "llm"),
    "LLM_PROXY_URL": (
        "LLM_PROXY_URL",
        "str",
        "",
        "",
        "http://litellm:4000/v1",
        "llm",
    ),
    "LLM_PROXY_PORT": ("LLM_PROXY_PORT", "int", 1, 65535, 4000, "llm"),
    "LLM_PROXY_CONTAINER": (
        "LLM_PROXY_CONTAINER",
        "str",
        "",
        "",
        "peon-litellm",
        "llm",
    ),
    "LLM_PROXY_NETWORK": (
        "LLM_PROXY_NETWORK",
        "str",
        "",
        "",
        "peon_default",
        "llm",
    ),
    "LITELLM_MASTER_KEY": (
        "LITELLM_MASTER_KEY",
        "str",
        "",
        "",
        "sk-peon-litellm",
        "llm",
    ),
    "OPENCODE_MODEL": ("OPENCODE_MODEL", "str", "", "", "peon/default", "llm"),
    # Sandbox
    "SANDBOX_IMAGE": (
        "SANDBOX_IMAGE",
        "str",
        "",
        "",
        "peon-sandbox:local",
        "sandbox",
    ),
    "OPENSHELL_IMAGE": (
        "OPENSHELL_IMAGE",
        "str",
        "",
        "",
        "peon-sandbox:local",
        "sandbox",
    ),
    "LEARN_LAB_IMAGE": (
        "LEARN_LAB_IMAGE",
        "str",
        "",
        "",
        "peon-sandbox:local",
        "sandbox",
    ),
    "PROJECT_SANDBOX_PREFIX": (
        "PROJECT_SANDBOX_PREFIX",
        "str",
        "",
        "",
        "peon-project",
        "sandbox",
    ),
    # Paths
    "PROJECT_WORKSPACES_DIR": (
        "PROJECT_WORKSPACES_DIR",
        "str",
        "",
        "",
        "workspaces",
        "paths",
    ),
    "SKILLS_DIR": ("SKILLS_DIR", "str", "", "", "skills", "paths"),
    "SKILLS_IMPORTED_DIR": (
        "SKILLS_IMPORTED_DIR",
        "str",
        "",
        "",
        "skills_imported",
        "paths",
    ),
    "HELPERS_DIR": ("HELPERS_DIR", "str", "", "", "helpers", "paths"),
    "TOOLS_CATALOG_DIR": (
        "TOOLS_CATALOG_DIR",
        "str",
        "",
        "",
        "tools/catalog",
        "paths",
    ),
    # System
    "DEBUG": ("DEBUG", "bool", 0, 1, 1, "system"),
    "ALLOWED_HOSTS": (
        "ALLOWED_HOSTS",
        "str",
        "",
        "",
        "localhost,127.0.0.1,web,edge",
        "system",
    ),
    "WEB_PORT": ("WEB_PORT", "int", 1, 65535, 8000, "system"),
    "PUBLIC_HOST": ("PUBLIC_HOST", "str", "", "", "localhost", "system"),
    "PUBLIC_URL": (
        "PUBLIC_URL",
        "str",
        "",
        "",
        "https://localhost:8000",
        "system",
    ),
    "CSRF_TRUSTED_ORIGINS": (
        "CSRF_TRUSTED_ORIGINS",
        "str",
        "",
        "",
        "https://localhost:8000,https://127.0.0.1:8000",
        "system",
    ),
}

_LABELS: dict[str, tuple[str, str]] = {
    "MAX_PARALLEL_PROJECTS": (
        "Max parallel projects",
        "Distinct projects that may have RUNNING jobs at once.",
    ),
    "MAX_AGENTS_PER_PROJECT": (
        "Max agents per project",
        "RUNNING jobs (root + subagents + parallel objectives) for one project.",
    ),
    "DRAMATIQ_MAX_THREADS": (
        "Dramatiq max worker threads",
        "Upper bound on Dramatiq worker threads (--threads). Requires Peon restart.",
    ),
    "DRAMATIQ_ENABLED": (
        "Dramatiq enabled",
        "Use Dramatiq actors (false = poll fallback). Requires Peon restart.",
    ),
    "DRAMATIQ_TIME_LIMIT_MS": (
        "Dramatiq time limit (ms)",
        "Soft wall-clock per actor run. Requires Peon restart.",
    ),
    "DRAMATIQ_MAX_RETRIES": (
        "Dramatiq max retries",
        "Broker retries per failed actor. Requires Peon restart.",
    ),
    "JOB_STUCK_RUNNING_SECONDS": (
        "Stuck job reclaim (seconds)",
        "RUNNING jobs idle longer than this are failed so the queue can continue.",
    ),
    "REDIS_URL": (
        "Redis URL",
        "Dramatiq broker URL. Requires Peon restart.",
    ),
    "DURABLE_SCHEDULER_ENABLED": (
        "Durable scheduler enabled",
        "Fire due ProjectSchedule rows via Dramatiq.",
    ),
    "DURABLE_SCHEDULER_INTERVAL_MS": (
        "Durable scheduler interval (ms)",
        "Delay between due-scan passes. Requires Peon restart to re-arm cadence.",
    ),
    "AGENT_MODULE": (
        "Agent module",
        "Orchestration module (crewai). Requires Peon restart.",
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
        "Max concurrent child Jobs via spawn_work.",
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
        "When RoleSpec.reasoning is true — PlanningConfig effort (low/medium/high).",
    ),
    "CREW_REASONING_MAX_ATTEMPTS": (
        "CrewAI reasoning max attempts",
        "When RoleSpec.reasoning is true — max planning refine attempts.",
    ),
    "COGNEE_ENABLED": (
        "Cognee memory enabled",
        "Self-hosted Cognee sidecar for engagement memory + peon recall.",
    ),
    "PEON_MEMORY_ENABLED": (
        "Peon memory dataset enabled",
        "Cross-project peon dataset (recall on new project create).",
    ),
    "PEON_DREAMING_ENABLED": (
        "Cognee dreaming enabled",
        "Background improve() cycles that consolidate sessions into long-term memory.",
    ),
    "PEON_DREAMING_INTERVAL_S": (
        "Dreaming interval (seconds)",
        "How often Peon runs a Cognee dream cycle (improve). Min 60. Applies on the next re-arm.",
    ),
    "COGNEE_PORT": (
        "Cognee port",
        "Single port for Cognee (Compose listen + host publish). Peon calls http://cognee:<port>. Must differ from WEB_PORT and LLM_PROXY_PORT. Requires compose recreate.",
    ),
    "COGNEE_HTTP_TIMEOUT_S": (
        "Cognee HTTP timeout (s)",
        "Client timeout for remember/recall/improve calls.",
    ),
    "COGNEE_EMBEDDING_MODEL": (
        "Cognee embedding model",
        "OpenRouter embedding model for cognify (chat uses LITELLM_MODEL). Requires cognee recreate.",
    ),
    "COGNEE_EMBEDDING_DIMENSIONS": (
        "Cognee embedding dimensions",
        "Vector size for the embedding model. Requires cognee recreate.",
    ),
    "LLM_PROVIDER": (
        "LLM provider",
        "Upstream provider id (openrouter, openai, …). Requires Peon restart.",
    ),
    "LLM_MODULE": (
        "LLM module",
        "In-process provider module (litellm). Requires Peon restart.",
    ),
    "LITELLM_MODEL": (
        "LiteLLM model",
        "Single chat model for Peon agents, Cognee sidecar, and the optional OpenCode proxy. Requires Peon/cognee recreate.",
    ),
    "LLM_TEMPERATURE": (
        "LLM temperature",
        "Sampling temperature for chat/planning. Requires Peon restart.",
    ),
    "LLM_PROXY_ENABLED": (
        "LiteLLM proxy enabled",
        "Required for Cognee + OpenCode Lab. Starts peon-litellm on the Compose network.",
    ),
    "LLM_PROXY_URL": (
        "LiteLLM proxy URL",
        "OpenAI-compatible /v1 URL. Requires Peon restart.",
    ),
    "LLM_PROXY_PORT": (
        "LiteLLM proxy host port",
        "Published host port for the proxy. Must differ from WEB_PORT and COGNEE_PORT. Requires Peon restart / container recreate.",
    ),
    "LLM_PROXY_CONTAINER": (
        "LiteLLM proxy container name",
        "Docker name for Settings-started gateway. Requires Peon restart.",
    ),
    "LLM_PROXY_NETWORK": (
        "LiteLLM proxy network",
        "Compose network for peon-litellm. Requires Peon restart.",
    ),
    "LITELLM_MASTER_KEY": (
        "LiteLLM master key",
        "Bearer key clients (Cognee/OpenCode) send to /v1. Requires Peon restart.",
    ),
    "OPENCODE_MODEL": (
        "OpenCode model",
        "Model id inside the Lab Toolsmith OpenCode session. Requires Peon restart.",
    ),
    "SANDBOX_IMAGE": (
        "Sandbox image",
        "Docker image for project sandboxes. Requires Peon restart.",
    ),
    "OPENSHELL_IMAGE": (
        "OpenShell image",
        "Docker image for OpenShell runtime. Requires Peon restart.",
    ),
    "LEARN_LAB_IMAGE": (
        "Learn lab image",
        "Docker image for Lab Toolsmith install tests. Requires Peon restart.",
    ),
    "PROJECT_SANDBOX_PREFIX": (
        "Project sandbox prefix",
        "Docker container name prefix. Requires Peon restart.",
    ),
    "PROJECT_WORKSPACES_DIR": (
        "Project workspaces dir",
        "Root for project + platform workspaces. Requires Peon restart.",
    ),
    "SKILLS_DIR": (
        "Skills dir",
        "Skill packs root (SKILL.md). Requires Peon restart.",
    ),
    "SKILLS_IMPORTED_DIR": (
        "Imported skills dir",
        "Claude Agent Skills packs (SKILL.md). Requires Peon restart.",
    ),
    "HELPERS_DIR": (
        "Helpers dir",
        "Shared pack helpers for sandboxes. Requires Peon restart.",
    ),
    "TOOLS_CATALOG_DIR": (
        "Tools catalog dir",
        "YAML tool catalog path. Requires Peon restart.",
    ),
    "DEBUG": (
        "Debug",
        "Django DEBUG. Requires Peon restart.",
    ),
    "ALLOWED_HOSTS": (
        "Allowed hosts",
        "Comma-separated Host headers. Requires Peon restart.",
    ),
    "WEB_PORT": (
        "Web port",
        "Host port for the HTTPS edge (Caddy → Django). Must differ from COGNEE_PORT and LLM_PROXY_PORT. Requires compose recreate.",
    ),
    "PUBLIC_HOST": (
        "Public host",
        "Hostname for edge TLS / Caddy. Requires compose recreate.",
    ),
    "PUBLIC_URL": (
        "Public URL",
        "Canonical public origin. Requires Peon restart.",
    ),
    "CSRF_TRUSTED_ORIGINS": (
        "CSRF trusted origins",
        "Comma-separated origins allowed to POST. Requires Peon restart.",
    ),
}


def _django_raw(attr: str) -> Any:
    """Read Django setting; support legacy DRAMATIQ_THREADS alias."""
    if attr == "DRAMATIQ_MAX_THREADS":
        if hasattr(settings, "DRAMATIQ_MAX_THREADS"):
            return getattr(settings, "DRAMATIQ_MAX_THREADS")
        return getattr(settings, "DRAMATIQ_THREADS", None)
    raw = getattr(settings, attr, None)
    # List-valued Django settings → comma string for the UI.
    if attr in {"ALLOWED_HOSTS", "CSRF_TRUSTED_ORIGINS"} and isinstance(raw, (list, tuple)):
        return ",".join(str(x).strip() for x in raw if str(x).strip())
    return raw


def _env_default(key: str) -> Any:
    attr, kind, lo, hi, fallback, _cat = _SPECS[key]
    raw = _django_raw(attr)
    if kind == "bool":
        if raw is None:
            return bool(fallback)
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    if kind == "choice":
        choices = tuple(lo) if isinstance(lo, (tuple, list)) else ()
        val = str(raw if raw is not None else fallback).strip().lower()
        return val if val in choices else str(fallback)
    if kind == "str":
        if raw is None:
            return str(fallback)
        if hasattr(raw, "__fspath__"):
            return str(raw)
        text = str(raw).strip()
        return text if text else str(fallback)
    if kind == "float":
        try:
            val = float(raw) if raw is not None else float(fallback)
        except (TypeError, ValueError):
            val = float(fallback)
        return max(float(lo), min(float(hi), val))
    # int
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
        # Migrate legacy DRAMATIQ_THREADS → DRAMATIQ_MAX_THREADS once.
        stored = obj.values
        if not isinstance(stored, dict):
            return obj
        changed = False
        if "DRAMATIQ_THREADS" in stored and "DRAMATIQ_MAX_THREADS" not in stored:
            stored = dict(stored)
            stored["DRAMATIQ_MAX_THREADS"] = stored.pop("DRAMATIQ_THREADS")
            changed = True
        if "PEON_DREAMING_INTERVAL_MS" in stored and "PEON_DREAMING_INTERVAL_S" not in stored:
            stored = dict(stored)
            try:
                ms = int(stored.pop("PEON_DREAMING_INTERVAL_MS") or 900_000)
            except (TypeError, ValueError):
                ms = 900_000
            stored["PEON_DREAMING_INTERVAL_S"] = max(60, ms // 1000)
            changed = True
        elif "PEON_DREAMING_INTERVAL_MS" in stored:
            stored = dict(stored)
            stored.pop("PEON_DREAMING_INTERVAL_MS", None)
            changed = True
        # Drop retired free-form base URL; port alone drives http://cognee:<port>.
        if "COGNEE_API_BASE" in stored:
            stored = dict(stored)
            stored.pop("COGNEE_API_BASE", None)
            changed = True
        if changed:
            obj.values = stored
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
        # Legacy key in older DB rows.
        if "DRAMATIQ_THREADS" in stored and "DRAMATIQ_MAX_THREADS" not in stored:
            stored = dict(stored)
            stored["DRAMATIQ_MAX_THREADS"] = stored.get("DRAMATIQ_THREADS")
        for key in _SPECS:
            if key in stored and stored[key] is not None:
                out[key] = cls._coerce(key, stored[key])
        return out

    @classmethod
    def get(cls, key: str) -> Any:
        # Accept legacy alias for callers.
        if key == "DRAMATIQ_THREADS":
            key = "DRAMATIQ_MAX_THREADS"
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
    def get_float(cls, key: str, default: float = 0.0) -> float:
        try:
            return float(cls.get(key))
        except (TypeError, ValueError):
            return float(default)

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
        if kind == "str":
            text = str(raw).strip() if raw is not None else ""
            return text if text else str(fallback)
        if kind == "float":
            try:
                val = float(raw)
            except (TypeError, ValueError):
                val = float(fallback)
            return max(float(lo), min(float(hi), val))
        try:
            val = int(raw)
        except (TypeError, ValueError):
            val = int(fallback)
        return max(int(lo), min(int(hi), val))

    @classmethod
    def host_port_conflict(cls, values: dict[str, Any] | None = None) -> str:
        """Return an error if published host ports collide; else empty string."""
        vals = values if values is not None else cls.as_dict()
        labels = {
            "WEB_PORT": "Web port (edge/Django UI)",
            "COGNEE_PORT": "Cognee port",
            "LLM_PROXY_PORT": "LiteLLM proxy host port",
        }
        by_port: dict[int, list[str]] = {}
        for key, label in labels.items():
            try:
                port = int(vals.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if port <= 0:
                continue
            by_port.setdefault(port, []).append(label)
        clashes = [f"{port}: {', '.join(names)}" for port, names in by_port.items() if len(names) > 1]
        if not clashes:
            return ""
        return "Host port conflict — each service needs a unique host port. " + "; ".join(clashes)

    @classmethod
    def update(cls, updates: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Merge updates; return (new values, restart-required keys that changed).

        Raises ValueError when WEB_PORT / COGNEE_PORT / LLM_PROXY_PORT collide.
        """
        row = cls.row()
        current = cls.as_dict()
        merged = dict(current)
        restart_changed: list[str] = []
        proxy_changed = False
        for key, raw in (updates or {}).items():
            if key == "DRAMATIQ_THREADS":
                key = "DRAMATIQ_MAX_THREADS"
            if key not in _SPECS:
                continue
            new = cls._coerce(key, raw)
            if key in RESTART_REQUIRED and current.get(key) != new:
                restart_changed.append(key)
            if key == "LLM_PROXY_ENABLED" and current.get(key) != new:
                proxy_changed = True
            merged[key] = new
        conflict = cls.host_port_conflict(merged)
        if conflict:
            raise ValueError(conflict)
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
            elif kind == "str":
                row["min"] = ""
                row["max"] = ""
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
