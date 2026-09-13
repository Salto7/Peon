"""Django app config + Dramatiq broker bootstrap."""

from __future__ import annotations

import logging

import dramatiq
from django.apps import AppConfig
from django.conf import settings
from dramatiq.brokers.redis import RedisBroker
from dramatiq.brokers.stub import StubBroker

logger = logging.getLogger(__name__)


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
        configure_broker()
