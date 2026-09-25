"""Django app config + Dramatiq broker bootstrap."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import dramatiq
from django.apps import AppConfig
from django.conf import settings
from dramatiq.brokers.redis import RedisBroker
from dramatiq.brokers.stub import StubBroker

logger = logging.getLogger(__name__)


def configure_orchestrator() -> None:
    """Push Django settings into orchestrator.RuntimeConfig (idempotent)."""
    from orchestrator.config import LLM_PROVIDER_KEY_ENV, RuntimeConfig, configure

    provider = str(getattr(settings, "LLM_PROVIDER", "openrouter") or "openrouter")
    key_attr = LLM_PROVIDER_KEY_ENV.get(provider, "LITELLM_API_KEY")
    api_key = (
        str(getattr(settings, key_attr, "") or "")
        or str(getattr(settings, "LITELLM_API_KEY", "") or "")
        or str(getattr(settings, "OPENROUTER_API_KEY", "") or "")
        or ""
    )
    external = [
        Path(os.path.expandvars(os.path.expanduser(str(p)))).resolve()
        for p in (getattr(settings, "SKILLS_EXTERNAL_DIRS", None) or [])
        if str(p).strip()
    ]

    configure(
        RuntimeConfig(
            skills_dir=Path(settings.SKILLS_DIR).resolve(),
            skills_external_dirs=external,
            tools_catalog_dir=Path(settings.TOOLS_CATALOG_DIR).resolve(),
            workspaces_dir=Path(settings.PROJECT_WORKSPACES_DIR).resolve(),
            llm_provider=provider,
            litellm_model=str(
                getattr(settings, "LITELLM_MODEL", None)
                or getattr(settings, "LLM_MODEL", None)
                or "openrouter/openai/gpt-4o-mini"
            ),
            litellm_api_key=api_key,
            litellm_api_base=getattr(settings, "LITELLM_API_BASE", None) or None,
            llm_temperature=float(getattr(settings, "LLM_TEMPERATURE", 0.0) or 0.0),
            llm_max_tokens=getattr(settings, "LLM_MAX_TOKENS", None),
            agent_max_iterations=int(getattr(settings, "AGENT_MAX_ITERATIONS", 40) or 40),
            agent_max_failure_replans=int(
                getattr(settings, "AGENT_MAX_FAILURE_REPLANS", 2) or 2
            ),
            agent_max_subagents=int(getattr(settings, "AGENT_MAX_SUBAGENTS", 4) or 4),
            agent_max_subagent_depth=int(
                getattr(settings, "AGENT_MAX_SUBAGENT_DEPTH", 2) or 2
            ),
            agent_runtime_enabled=bool(getattr(settings, "AGENT_RUNTIME_ENABLED", True)),
            sandbox_enabled=bool(getattr(settings, "SANDBOX_ENABLED", True)),
            sandbox_image=str(getattr(settings, "SANDBOX_IMAGE", "peon-sandbox:local")),
            sandbox_prefix=str(
                getattr(settings, "PROJECT_SANDBOX_PREFIX", "peon-project")
            ),
        )
    )


def configure_broker() -> None:
    """Idempotent broker setup — call from AppConfig.ready() / worker import."""
    enabled = bool(getattr(settings, "DRAMATIQ_ENABLED", True))
    url = str(getattr(settings, "REDIS_URL", "redis://127.0.0.1:6379/0"))
    done = getattr(configure_broker, "_done", None)
    if done == (enabled, url):
        return

    time_limit = int(getattr(settings, "DRAMATIQ_TIME_LIMIT_MS", 6 * 60 * 60 * 1000) or 0)
    max_retries = int(getattr(settings, "DRAMATIQ_MAX_RETRIES", 3) or 3)
    middleware: list = [
        dramatiq.middleware.TimeLimit(time_limit=time_limit),
        dramatiq.middleware.Retries(
            max_retries=max_retries,
            min_backoff=15_000,
            max_backoff=300_000,
        ),
        dramatiq.middleware.Callbacks(),
        dramatiq.middleware.Pipelines(),
    ]

    if not enabled:
        broker = StubBroker(middleware=middleware)
    else:
        broker = RedisBroker(url=url, middleware=middleware)

    dramatiq.set_broker(broker)
    configure_broker._done = (enabled, url)  # type: ignore[attr-defined]
    logger.info("dramatiq broker ready enabled=%s url=%s", enabled, url if enabled else "-")


class ProjectsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "peon.projects"
    label = "projects"
    verbose_name = "Projects"

    def ready(self) -> None:
        # Broker only — do not import tasks here (dramatiq worker imports tasks
        # which may call django.setup(); nested populate() would fail).
        # LocalSkillExecutor is registered from run_job via ensure_skill_executor().
        configure_orchestrator()
        configure_broker()
