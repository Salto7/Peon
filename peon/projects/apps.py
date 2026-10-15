"""Django app config + Dramatiq broker bootstrap."""

from __future__ import annotations

import logging
from pathlib import Path

import dramatiq
from django.apps import AppConfig
from django.conf import settings
from django.core.checks import Error, register
from dramatiq.brokers.redis import RedisBroker
from dramatiq.brokers.stub import StubBroker

from agent_runtime.registry import RuntimeRegistry
from orchestrator.config import LLM_PROVIDER_KEY_ENV, RuntimeConfig, configure
from orchestrator.crew.roles.registry import RoleRegistry

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
                hint="Check Lab → Roles / PlatformCatalog.",
                id="projects.E001",
            )
        ]

    errors = []
    if not roles:
        errors.append(
            Error(
                "No engagement roles found in the platform database.",
                hint="Open Lab → Roles or run ensure_seeded().",
                id="projects.E002",
            )
        )
        return errors
    if registry.manager() is None:
        errors.append(
            Error(
                "No engagement manager role.",
                hint="Add a role with kind engagement-pm in Lab → Roles.",
                id="projects.E003",
            )
        )
    if registry.analyzer() is None:
        errors.append(
            Error(
                "No analyzer role.",
                hint="Add a role with kind analyzer in Lab → Roles.",
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
            skills_dir=Path(settings.SKILLS_DIR).resolve(),
            skills_imported_dir=Path(settings.SKILLS_IMPORTED_DIR).resolve(),
            skills_extra_dirs=tuple(
                Path(path).resolve()
                for path in getattr(settings, "SKILLS_EXTRA_DIRS", ())
            ),
            mcps_dir=Path(
                getattr(settings, "MCPS_DIR", None) or (Path(settings.BASE_DIR) / "mcps")
            ).resolve(),
            tools_catalog_dir=Path(settings.TOOLS_CATALOG_DIR).resolve(),
            workspaces_dir=Path(settings.PROJECT_WORKSPACES_DIR).resolve(),
            platform_workspace_dir=Path(
                settings.PLATFORM_WORKSPACE_DIR
            ).resolve(),
            llm_provider=provider,
            llm_module=str(
                getattr(settings, "LLM_MODULE", None) or "litellm"
            ).strip()
            or "litellm",
            litellm_model=str(
                getattr(settings, "LITELLM_MODEL", None)
                or getattr(settings, "LLM_MODEL", None)
                or "openrouter/openai/gpt-5.6-luna"
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


def _process_kind() -> str:
    """``web`` | ``worker`` for long-running servers, else ``""``.

    Boot side effects (catalog seed, LLM proxy container, scheduler chains,
    CrewAI import) are skipped for one-off commands (``migrate``, ``shell``,
    ``check``, scripts) and for the ``runserver`` autoreload *parent*, which
    only watches files — otherwise every reload repeats them twice.
    """
    import os
    import sys

    argv = [a.lower() for a in sys.argv]
    prog = os.path.basename(argv[0]) if argv else ""
    command = argv[1] if len(argv) > 1 else ""
    if prog == "dramatiq" or (prog == "manage.py" and command == "run_worker"):
        return "worker"
    if prog == "manage.py":
        if command == "runserver" and (
            os.environ.get("RUN_MAIN") == "true" or "--noreload" in argv
        ):
            return "web"
        return ""
    # gunicorn / uvicorn; not ``python -c`` or stdin scripts.
    return "" if prog in {"", "-", "-c"} else "web"


def _boot_services(kind: str) -> None:
    """Runs on a thread once app loading finishes.

    ``peon.projects.tasks`` calls ``django.setup()`` at import time (and is
    still half-imported while the Dramatiq CLI loads it), so importing it from
    ``ready()`` always failed — the scanners never started.
    """
    import time

    from django.apps import apps

    deadline = time.monotonic() + 60
    while not apps.ready and time.monotonic() < deadline:
        time.sleep(0.2)
    time.sleep(1.0)  # let the worker finish importing its actor module
    if str(getattr(settings, "AGENT_MODULE", "crewai") or "crewai").strip().lower() == "crewai":
        try:
            import crewai  # noqa: F401 — pay the import cost before the first job
        except Exception as exc:
            logger.warning("crewai warm import skipped: %s", exc)
    try:
        from peon.projects.llm_proxy import LlmProxy
        from peon.projects.runtime_settings import PeonSettings

        if PeonSettings.get_bool("LLM_PROXY_ENABLED", True):
            LlmProxy.shared().ensure()
    except Exception as exc:
        logger.warning("llm proxy kick skipped: %s", exc)
    try:
        from peon.projects.tasks import ensure_dream_scheduler, ensure_durable_scheduler

        # The worker runs the actors: on its boot, replace whatever chain exists
        # (it may be from older code); the web process only fills a gap.
        force = kind == "worker"
        logger.info(
            "schedulers: durable=%s dream=%s",
            ensure_durable_scheduler(force=force),
            ensure_dream_scheduler(force=force),
        )
    except Exception as exc:
        logger.warning("scheduler kick failed: %s", exc)



def _watch_vite_manifest() -> None:
    """Restart runserver when ``vite build --watch`` rewrites the manifest.

    django-vite reads manifest.json once per process, so new asset hashes are
    only picked up after a reload.
    """
    from django.utils.autoreload import autoreload_started

    if not settings.DEBUG:
        return
    manifest = Path(settings.DJANGO_VITE["default"]["manifest_path"])

    def watch(sender, **kwargs):
        sender.extra_files.add(manifest)

    autoreload_started.connect(watch, weak=False)

class ProjectsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "peon.projects"
    label = "projects"
    verbose_name = "Projects"

    def ready(self) -> None:
        import threading

        from peon.projects.db import configure_sqlite_concurrency

        configure_sqlite_concurrency()
        _watch_vite_manifest()
        configure_orchestrator()
        configure_broker()
        RuntimeRegistry.shared().register_builtin()
        kind = _process_kind()
        if not kind:
            return
        try:
            from peon.projects.platform_catalog import PlatformCatalog

            PlatformCatalog.shared().ensure_seeded()
        except Exception as exc:
            logger.warning("platform catalog seed skipped: %s", exc)
        threading.Thread(
            target=_boot_services, args=(kind,), name="peon-boot", daemon=True
        ).start()
