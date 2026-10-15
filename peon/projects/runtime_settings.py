"""Operator-tunable runtime policy (DB row + .env defaults).

``.env`` / Django settings supply defaults; the Peon Settings row overrides them.
Settings marked ``restart`` need a Peon restart (web and/or worker) after save.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from functools import cache
from typing import Any

from django.conf import settings

from peon.projects.llm_proxy import LlmProxy
from peon.projects.models import RuntimeSettings

logger = logging.getLogger(__name__)

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
_TRUE = {"1", "true", "yes", "on"}
# Stored values are re-read at most this often per process; ``update`` clears it.
_CACHE_SECONDS = 2.0


@dataclass(frozen=True)
class _S:
    key: str
    kind: str  # int | float | bool | str | choice
    default: Any
    category: str
    label: str
    help: str
    lo: Any = None
    hi: Any = None
    choices: tuple[str, ...] = ()
    restart: bool = False

    def coerce(self, raw: Any, fallback: Any = None) -> Any:
        """Normalize ``raw`` to this setting's type; invalid → ``fallback`` (default)."""
        fallback = self.default if fallback is None else fallback
        if raw is None:
            raw = fallback
        if self.kind == "bool":
            return raw if isinstance(raw, bool) else str(raw).strip().lower() in _TRUE
        if self.kind == "choice":
            value = str(raw).strip().lower()
            return value if value in self.choices else str(fallback)
        if self.kind == "str":
            if isinstance(raw, (list, tuple)):  # ALLOWED_HOSTS-style Django lists
                raw = ",".join(str(x).strip() for x in raw if str(x).strip())
            return str(raw).strip() or str(fallback)
        cast = float if self.kind == "float" else int
        try:
            value = cast(raw)
        except (TypeError, ValueError):
            value = cast(fallback)
        return max(cast(self.lo), min(cast(self.hi), value))


SPECS: dict[str, _S] = {
    s.key: s
    for s in (
    # queue
    _S('MAX_PARALLEL_PROJECTS', 'int', 3, 'queue', 'Max parallel projects', 'Distinct projects that may have RUNNING jobs at once.', lo=1, hi=32),
    _S('MAX_AGENTS_PER_PROJECT', 'int', 8, 'queue', 'Max agents per project', 'RUNNING jobs (root + subagents + parallel objectives) for one project.', lo=1, hi=64),
    _S('DRAMATIQ_MAX_THREADS', 'int', 8, 'queue', 'Dramatiq max worker threads', 'Upper bound on Dramatiq worker threads (--threads). Requires Peon restart.', lo=1, hi=64, restart=True),
    _S('DRAMATIQ_ENABLED', 'bool', 1, 'queue', 'Dramatiq enabled', 'Use Dramatiq actors (false = poll fallback). Requires Peon restart.', restart=True),
    _S('DRAMATIQ_TIME_LIMIT_MS', 'int', 21600000, 'queue', 'Dramatiq time limit (ms)', 'Soft wall-clock per actor run. Requires Peon restart.', lo=60000, hi=86400000, restart=True),
    _S('DRAMATIQ_MAX_RETRIES', 'int', 3, 'queue', 'Dramatiq max retries', 'Broker retries per failed actor. Requires Peon restart.', lo=0, hi=20, restart=True),
    _S('JOB_STUCK_RUNNING_SECONDS', 'int', 7200, 'queue', 'Stuck job reclaim (seconds)', 'RUNNING jobs idle longer than this are failed so the queue can continue.', lo=60, hi=86400),
    _S('REDIS_URL', 'str', 'redis://redis:6379/0', 'queue', 'Redis URL', 'Dramatiq broker URL. Requires Peon restart.', restart=True),
    _S('DURABLE_SCHEDULER_ENABLED', 'bool', 1, 'queue', 'Durable scheduler enabled', 'Fire due ProjectSchedule rows via Dramatiq.'),
    _S('DURABLE_SCHEDULER_INTERVAL_MS', 'int', 60000, 'queue', 'Durable scheduler interval (ms)', 'Delay between due-scan passes. Requires Peon restart to re-arm cadence.', lo=15000, hi=3600000, restart=True),
    # agent
    _S('AGENT_MODULE', 'choice', 'crewai', 'agent', 'Agent module', 'Orchestration module (crewai). Requires Peon restart.', choices=('crewai',), restart=True),
    _S('AGENT_MAX_FAILURE_REPLANS', 'int', 2, 'agent', 'Agent failure replans', 'PM recovery replans allowed after failed specialist objective jobs.', lo=0, hi=20),
    _S('AGENT_MAX_ITERATIONS', 'int', 40, 'agent', 'Agent max iterations', 'Hard cap on CrewAI max_iter per Job (roles may set a lower value).', lo=1, hi=500),
    _S('AGENT_MAX_SUBAGENTS', 'int', 8, 'agent', 'Agent max subagents', 'Max concurrent child Jobs via spawn_work.', lo=0, hi=32),
    _S('AGENT_MAX_SUBAGENT_DEPTH', 'int', 2, 'agent', 'Agent max subagent depth', 'Max Job parent→child depth (2 = root + one level).', lo=1, hi=8),
    _S('AGENT_RUNTIME_ENABLED', 'bool', 1, 'agent', 'Agent runtime enabled', 'Emergency kill switch for CrewAI job / project agents.'),
    # crew
    _S('CREW_REASONING_EFFORT', 'choice', 'low', 'crew', 'CrewAI reasoning effort', 'When RoleSpec.reasoning is true — PlanningConfig effort (low/medium/high).', choices=('low', 'medium', 'high')),
    _S('CREW_REASONING_MAX_ATTEMPTS', 'int', 1, 'crew', 'CrewAI reasoning max attempts', 'When RoleSpec.reasoning is true — max planning refine attempts.', lo=1, hi=5),
    # memory
    _S('COGNEE_ENABLED', 'bool', 1, 'memory', 'Cognee memory enabled', 'Self-hosted Cognee sidecar for engagement memory + peon recall.'),
    _S('PEON_MEMORY_ENABLED', 'bool', 1, 'memory', 'Peon memory dataset enabled', 'Cross-project peon dataset (recall on new project create).'),
    _S('PEON_DREAMING_ENABLED', 'bool', 1, 'memory', 'Cognee dreaming enabled', 'Background improve() cycles that consolidate sessions into long-term memory.'),
    _S('PEON_DREAMING_INTERVAL_S', 'int', 900, 'memory', 'Dreaming interval (seconds)', 'How often Peon runs a Cognee dream cycle (improve). Min 60. Applies on the next re-arm.', lo=60, hi=86400),
    _S('COGNEE_PORT', 'int', 8001, 'memory', 'Cognee port', 'Single port for Cognee (Compose listen + host publish). Peon calls http://cognee:<port>. Must differ from WEB_PORT and LLM_PROXY_PORT. Requires compose recreate.', lo=1, hi=65535, restart=True),
    _S('COGNEE_HTTP_TIMEOUT_S', 'float', 120, 'memory', 'Cognee HTTP timeout (s)', 'Client timeout for remember/recall/improve calls.', lo=5, hi=600),
    _S('COGNEE_EMBEDDING_MODEL', 'str', 'openrouter/openai/text-embedding-3-small', 'memory', 'Cognee embedding model', 'OpenRouter embedding model for cognify (chat uses LITELLM_MODEL). Requires cognee recreate.', restart=True),
    _S('COGNEE_EMBEDDING_DIMENSIONS', 'int', 1536, 'memory', 'Cognee embedding dimensions', 'Vector size for the embedding model. Requires cognee recreate.', lo=64, hi=8192, restart=True),
    # llm
    _S('LLM_PROVIDER', 'str', 'openrouter', 'llm', 'LLM provider', 'Upstream provider id (openrouter, openai, …). Requires Peon restart.', restart=True),
    _S('LLM_MODULE', 'str', 'litellm', 'llm', 'LLM module', 'In-process provider module (litellm). Requires Peon restart.', restart=True),
    _S('LITELLM_MODEL', 'str', 'openrouter/openai/gpt-5.6-luna', 'llm', 'LiteLLM model', 'Single chat model for Peon agents, Cognee sidecar, and the optional OpenCode proxy. Requires Peon/cognee recreate.', restart=True),
    _S('LLM_TEMPERATURE', 'float', 0, 'llm', 'LLM temperature', 'Sampling temperature for chat/planning. Requires Peon restart.', lo=0, hi=2, restart=True),
    _S('LLM_PROXY_ENABLED', 'bool', 1, 'llm', 'LiteLLM proxy enabled', 'Required for Cognee + OpenCode Lab. Starts peon-litellm on the Compose network.'),
    _S('LLM_PROXY_URL', 'str', 'http://litellm:4000/v1', 'llm', 'LiteLLM proxy URL', 'OpenAI-compatible /v1 URL. Requires Peon restart.', restart=True),
    _S('LLM_PROXY_PORT', 'int', 4000, 'llm', 'LiteLLM proxy host port', 'Published host port for the proxy. Must differ from WEB_PORT and COGNEE_PORT. Requires Peon restart / container recreate.', lo=1, hi=65535, restart=True),
    _S('LLM_PROXY_CONTAINER', 'str', 'peon-litellm', 'llm', 'LiteLLM proxy container name', 'Docker name for Settings-started gateway. Requires Peon restart.', restart=True),
    _S('LLM_PROXY_NETWORK', 'str', 'peon_default', 'llm', 'LiteLLM proxy network', 'Compose network for peon-litellm. Requires Peon restart.', restart=True),
    _S('LITELLM_MASTER_KEY', 'str', 'sk-peon-litellm', 'llm', 'LiteLLM master key', 'Bearer key clients (Cognee/OpenCode) send to /v1. Requires Peon restart.', restart=True),
    _S('OPENCODE_MODEL', 'str', 'peon/default', 'llm', 'OpenCode model', 'Model id inside the Lab Toolsmith OpenCode session. Requires Peon restart.', restart=True),
    # sandbox
    _S('SANDBOX_IMAGE', 'str', 'peon-sandbox:local', 'sandbox', 'Sandbox image', 'Docker image for project sandboxes. Requires Peon restart.', restart=True),
    _S('OPENSHELL_IMAGE', 'str', 'peon-sandbox:local', 'sandbox', 'OpenShell image', 'Docker image for OpenShell runtime. Requires Peon restart.', restart=True),
    _S('LEARN_LAB_IMAGE', 'str', 'peon-sandbox:local', 'sandbox', 'Learn lab image', 'Docker image for Lab Toolsmith install tests. Requires Peon restart.', restart=True),
    _S('PROJECT_SANDBOX_PREFIX', 'str', 'peon-project', 'sandbox', 'Project sandbox prefix', 'Docker container name prefix. Requires Peon restart.', restart=True),
    # paths
    _S('PROJECT_WORKSPACES_DIR', 'str', 'workspaces', 'paths', 'Project workspaces dir', 'Root for project + platform workspaces. Requires Peon restart.', restart=True),
    _S('SKILLS_DIR', 'str', 'skills', 'paths', 'Skills dir', 'All skill packs (Peon + Claude Agent Skills). Type is inferred per pack. Requires Peon restart.', restart=True),
    _S('SKILLS_IMPORTED_DIR', 'str', 'skills', 'paths', 'Imported skills dir (legacy)', 'Deprecated alias of Skills dir. Leave unset to use skills/. Requires Peon restart.', restart=True),
    _S('MCPS_DIR', 'str', 'mcps', 'paths', 'MCP packs dir', 'Disk MCP packs (mcp.json / package.json). Refresh re-imports from here. Requires Peon restart.', restart=True),
    _S('TOOLS_CATALOG_DIR', 'str', 'tools/catalog', 'paths', 'Tools catalog dir', 'YAML tool catalog path. Requires Peon restart.', restart=True),
    # system
    _S('DEBUG', 'bool', 1, 'system', 'Debug', 'Django DEBUG. Requires Peon restart.', restart=True),
    _S('ALLOWED_HOSTS', 'str', 'localhost,127.0.0.1,web,edge', 'system', 'Allowed hosts', 'Comma-separated Host headers. Requires Peon restart.', restart=True),
    _S('WEB_PORT', 'int', 8000, 'system', 'Web port', 'Host port for the HTTPS edge (Caddy → Django). Must differ from COGNEE_PORT and LLM_PROXY_PORT. Requires compose recreate.', lo=1, hi=65535, restart=True),
    _S('PUBLIC_HOST', 'str', 'localhost', 'system', 'Public host', 'Hostname for edge TLS / Caddy. Requires compose recreate.', restart=True),
    _S('PUBLIC_URL', 'str', 'https://localhost:8000', 'system', 'Public URL', 'Canonical public origin. Requires Peon restart.', restart=True),
    _S('CSRF_TRUSTED_ORIGINS', 'str', 'https://localhost:8000,https://127.0.0.1:8000', 'system', 'CSRF trusted origins', 'Comma-separated origins allowed to POST. Requires Peon restart.', restart=True),
    )
}
RESTART_REQUIRED = frozenset(k for k, s in SPECS.items() if s.restart)
_PUBLISHED_PORTS = {
    "WEB_PORT": "Web port (edge/Django UI)",
    "COGNEE_PORT": "Cognee port",
    "LLM_PROXY_PORT": "LiteLLM proxy host port",
}
_cached: tuple[float, dict[str, Any]] | None = None


@cache
def _defaults() -> dict[str, Any]:
    """Django settings / .env values, typed and clamped (fixed per process)."""
    return {k: s.coerce(getattr(settings, k, None)) for k, s in SPECS.items()}


def _host_port_conflict(values: dict[str, Any]) -> str:
    by_port: dict[int, list[str]] = {}
    for key, label in _PUBLISHED_PORTS.items():
        port = int(values.get(key) or 0)
        if port > 0:
            by_port.setdefault(port, []).append(label)
    clashes = [f"{port}: {', '.join(n)}" for port, n in by_port.items() if len(n) > 1]
    if not clashes:
        return ""
    return "Host port conflict — each service needs a unique host port. " + "; ".join(clashes)


class PeonSettings:
    """Read/write the singleton RuntimeSettings row over env-backed defaults."""

    @classmethod
    def as_dict(cls) -> dict[str, Any]:
        global _cached
        now = time.monotonic()
        if _cached is not None and now - _cached[0] < _CACHE_SECONDS:
            return dict(_cached[1])
        values = dict(_defaults())
        try:
            row = RuntimeSettings.objects.filter(pk=1).only("values").first()
            stored = row.values if row is not None and isinstance(row.values, dict) else {}
        except Exception as exc:  # DB not migrated yet (first boot / migrate)
            logger.warning("runtime settings unavailable, using defaults: %s", exc)
            return values
        for key, spec in SPECS.items():
            if stored.get(key) is not None:
                values[key] = spec.coerce(stored[key], values[key])
        _cached = (now, values)
        return dict(values)

    @classmethod
    def get(cls, key: str) -> Any:
        return cls.as_dict().get(key)

    @classmethod
    def get_int(cls, key: str, default: int = 0) -> int:
        value = cls.get(key)
        return default if value is None else int(value)

    @classmethod
    def get_float(cls, key: str, default: float = 0.0) -> float:
        value = cls.get(key)
        return default if value is None else float(value)

    @classmethod
    def get_bool(cls, key: str, default: bool = True) -> bool:
        value = cls.get(key)
        return default if value is None else bool(value)

    @classmethod
    def get_str(cls, key: str, default: str = "") -> str:
        value = cls.get(key)
        return default if value is None else str(value)

    @classmethod
    def update(cls, updates: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Merge updates; return (new values, restart-required keys that changed).

        Raises ValueError when WEB_PORT / COGNEE_PORT / LLM_PROXY_PORT collide.
        """
        global _cached
        current = cls.as_dict()
        merged = dict(current)
        for key, raw in (updates or {}).items():
            if key in SPECS:
                merged[key] = SPECS[key].coerce(raw)
        conflict = _host_port_conflict(merged)
        if conflict:
            raise ValueError(conflict)
        RuntimeSettings.objects.update_or_create(pk=1, defaults={"values": merged})
        _cached = None
        changed = [k for k in SPECS if merged[k] != current[k]]
        if "LLM_PROXY_ENABLED" in changed:
            try:
                LlmProxy.shared().apply(bool(merged["LLM_PROXY_ENABLED"]))
            except Exception as exc:
                logger.warning("LLM proxy apply failed: %s", exc, exc_info=True)
        return merged, [k for k in changed if k in RESTART_REQUIRED]

    @classmethod
    def field_meta(cls) -> list[dict[str, Any]]:
        """Flat UI metadata for the settings form."""
        values, defaults = cls.as_dict(), _defaults()
        numeric = {"int", "float"}
        return [
            {
                "key": key,
                "label": spec.label,
                "help": spec.help,
                "restart_required": spec.restart,
                "kind": spec.kind,
                "category": spec.category,
                "value": values[key],
                "env_default": defaults[key],
                "min": spec.lo if spec.kind in numeric else (0 if spec.kind == "bool" else ""),
                "max": spec.hi if spec.kind in numeric else (1 if spec.kind == "bool" else ""),
                **({"choices": list(spec.choices)} if spec.kind == "choice" else {}),
            }
            for key, spec in SPECS.items()
        ]

    @classmethod
    def categorized_fields(cls) -> list[dict[str, Any]]:
        """Fields grouped for the settings page."""
        fields = cls.field_meta()
        groups = [
            {"id": cid, "label": label, "fields": [f for f in fields if f["category"] == cid]}
            for cid, label in CATEGORIES
        ]
        return [g for g in groups if g["fields"]]
