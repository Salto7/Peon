"""Django app config + Dramatiq broker bootstrap."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import dramatiq
from django.apps import AppConfig
from django.conf import settings
from django.core.checks import Error, register
from dramatiq.brokers.redis import RedisBroker
from dramatiq.brokers.stub import StubBroker

from agent_runtime.registry import register_builtin
from orchestrator.config import LLM_PROVIDER_KEY_ENV, RuntimeConfig, configure
from orchestrator.crew.roles.registry import RoleRegistry, analyzer_role, manager_role

logger = logging.getLogger(__name__)


@register()
def crewai_roles_check(app_configs, **kwargs):
    """Fail fast when CrewAI is configured without a usable role catalog."""
    del app_configs, kwargs
    module = str(getattr(settings, "AGENT_MODULE", "crewai") or "crewai").strip().lower()
    if module != "crewai":
        return []

    try:
        registry = RoleRegistry.shared()
        roles = registry.list_roles()
    except Exception as exc:
        return [
            Error(
                f"Could not load CrewAI roles: {exc}",
                hint="Check ROLES_DIR and every roles/*/ROLE.yaml file.",
                id="projects.E001",
            )
        ]

    errors = []
    roles_dir = registry.roles_dir()
    if not roles:
        errors.append(
            Error(
                f"No CrewAI roles found in {roles_dir}.",
                hint="Package or mount roles/ into both the web and worker services.",
                id="projects.E002",
            )
        )
        return errors
    if manager_role(registry) is None:
        errors.append(
            Error(
                "CrewAI role catalog has no engagement manager.",
                hint=(
                    "Add a non-authoring role with allow_delegation: true "
                    "and an empty hierarchy.reports_to."
                ),
                id="projects.E003",
            )
        )
    if analyzer_role(registry) is None:
        errors.append(
            Error(
                "CrewAI role catalog has no analyzer.",
                hint="Add a role whose capabilities include report or analyzer.",
                id="projects.E004",
            )
        )
    return errors


def configure_orchestrator() -> None:
    """Push Django settings into orchestrator.RuntimeConfig (idempotent)."""

    provider = str(getattr(settings, "LLM_PROVIDER", "openrouter") or "openrouter")
    key_attr = LLM_PROVIDER_KEY_ENV.get(provider, "LITELLM_API_KEY")
    api_key = (
        str(getattr(settings, key_attr, "") or "")
        or str(getattr(settings, "LITELLM_API_KEY", "") or "")
        or str(getattr(settings, "OPENROUTER_API_KEY", "") or "")
        or ""
    )
    configure(
        RuntimeConfig(
            roles_dir=Path(settings.ROLES_DIR).resolve(),
            helpers_dir=Path(settings.HELPERS_DIR).resolve(),
            tools_catalog_dir=Path(settings.TOOLS_CATALOG_DIR).resolve(),
            workspaces_dir=Path(settings.PROJECT_WORKSPACES_DIR).resolve(),
            llm_provider=provider,
            llm_module=str(
                getattr(settings, "LLM_MODULE", None) or "litellm"
            ).strip()
            or "litellm",
            litellm_model=str(
                getattr(settings, "LITELLM_MODEL", None)
                or getattr(settings, "LLM_MODEL", None)
                or "openrouter/openai/gpt-4o-mini"
            ),
            litellm_api_key=api_key,
            litellm_api_base=getattr(settings, "LITELLM_API_BASE", None) or None,
            llm_temperature=float(getattr(settings, "LLM_TEMPERATURE", 0.0) or 0.0),
            llm_max_tokens=getattr(settings, "LLM_MAX_TOKENS", None),
            llm_proxy_enabled=bool(getattr(settings, "LLM_PROXY_ENABLED", False)),
            llm_proxy_url=str(
                getattr(settings, "LLM_PROXY_URL", None) or "http://litellm:4000/v1"
            ).strip()
            or "http://litellm:4000/v1",
            agent_max_iterations=int(getattr(settings, "AGENT_MAX_ITERATIONS", 40) or 40),
            agent_max_failure_replans=int(
                getattr(settings, "AGENT_MAX_FAILURE_REPLANS", 2) or 2
            ),
            agent_max_subagents=int(getattr(settings, "AGENT_MAX_SUBAGENTS", 4) or 4),
            agent_max_subagent_depth=int(
                getattr(settings, "AGENT_MAX_SUBAGENT_DEPTH", 2) or 2
            ),
            agent_runtime_enabled=bool(getattr(settings, "AGENT_RUNTIME_ENABLED", True)),
            crew_reasoning_effort=str(
                getattr(settings, "CREW_REASONING_EFFORT", "low") or "low"
            )
            .strip()
            .lower(),
            crew_reasoning_max_attempts=int(
                getattr(settings, "CREW_REASONING_MAX_ATTEMPTS", 1) or 1
            ),
            agent_module=str(getattr(settings, "AGENT_MODULE", "crewai") or "crewai")
            .strip()
            .lower()
            or "crewai",
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
        configure_orchestrator()
        configure_broker()

        register_builtin()
        self._warm_crewai()

    @staticmethod
    def _warm_crewai() -> None:
        """Pay CrewAI import cost at process boot, not on the first job."""
        module = str(getattr(settings, "AGENT_MODULE", "crewai") or "crewai").strip().lower()
        if module != "crewai":
            return
        try:
            # deferred: startup warm
            import crewai  # noqa: F401
        except Exception as exc:
            logger.debug("crewai warm import skipped: %s", exc)
