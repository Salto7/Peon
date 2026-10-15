"""Dramatiq actors for long-running project jobs (sandbox execution).

Ensure Django + RedisBroker are configured before @actor registration so the
worker does not fall back to dramatiq's localhost Redis default.
"""

from __future__ import annotations

import logging
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "peon.config.settings")

import django
from django.apps import apps

if not apps.ready:
    # Dramatiq CLI path — AppConfig.ready() configures the broker.
    django.setup()
else:
    from peon.projects.apps import configure_broker

    configure_broker()

import dramatiq
from django.conf import settings
from django.db import close_old_connections

from agent_runtime.registry import RuntimeRegistry
from peon.projects.models import Job, JobStatus, SandboxRuntime
from peon.projects.sandbox import ProjectSandbox
from peon.projects.job_claim import job_slots_available, mark_job_running
from peon.projects.learn_lab import LearnLab

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="peon.jobs", max_retries=3, time_limit=6 * 60 * 60 * 1000)
def process_job(job_id: str) -> None:
    """Claim PENDING→RUNNING then run the job agent inside the project sandbox."""
    # circular: tasks ↔ job_run
    from peon.projects.job_run import finish_job, run_job

    close_old_connections()

    from peon.projects.db import get_job, using_project

    try:
        job = get_job(job_id)
    except Job.DoesNotExist:
        logger.warning("process_job: missing job %s", job_id)
        return

    with using_project(job.project_id):
        if job.status != JobStatus.PENDING:
            logger.info("process_job: skip non-pending job %s (%s)", job_id, job.status)
            return

        if job.objective_id and job.objective is not None:
            from peon.projects.objectives import ObjectiveScheduler

            if not ObjectiveScheduler().dependencies_met(job.objective):
                try:
                    process_job.send_with_options(args=(str(job_id),), delay=5_000)
                except Exception:
                    logger.info(
                        "process_job: dependencies unmet for %s; leave pending",
                        job_id,
                    )
                return

        if not job_slots_available(job):
            try:
                process_job.send_with_options(args=(str(job_id),), delay=5_000)
            except Exception:
                logger.info(
                    "process_job: slots full for %s; leave pending (no delay requeue)",
                    job_id,
                )
            return
        if mark_job_running(job) is None:
            logger.info("process_job: claim lost for %s", job_id)
            return

        try:
            run_job(job)
        except Exception as exc:
            logger.exception("process_job failed for %s", job_id)
            finish_job(job, status=JobStatus.FAILED, error=str(exc))
        finally:
            close_old_connections()


def _dramatiq_enabled() -> bool:
    return bool(getattr(settings, "DRAMATIQ_ENABLED", True))


def enqueue_job(job_id: str) -> bool:
    """Enqueue a job for Dramatiq when enabled; return False if caller should poll-run."""
    if not _dramatiq_enabled():
        return False
    process_job.send(str(job_id))
    return True


@dramatiq.actor(queue_name="peon.jobs", max_retries=3, time_limit=120_000)
def cleanup_project_sandbox(project_id: str, runtime_id: str = "") -> None:
    """Remove a project sandbox container on the worker (has docker.sock).

    Running ``docker rm`` inside the web request can flap Docker's network and
    interrupt the browser mid-redirect — so delete enqueues this instead.
    """
    close_old_connections()
    try:
        result = ProjectSandbox.remove(
            str(project_id),
            runtime_id=runtime_id,
        )
        logger.info("cleanup_project_sandbox %s → %s", project_id, result)
        if result.get("action") == "error":
            raise RuntimeError(str(result.get("errors") or "sandbox cleanup failed"))
    except Exception:
        logger.exception("cleanup_project_sandbox failed for %s", project_id)
        raise
    finally:
        close_old_connections()


def enqueue_sandbox_cleanup(project_id: str, *, runtime_id: str = "") -> dict:
    """Queue sandbox removal on the worker, or run inline when Dramatiq is off."""
    pid = str(project_id or "").strip()
    if not pid:
        return {"action": "skipped", "reason": "no project_id"}
    rid = SandboxRuntime.resolve(runtime_id)
    registry = RuntimeRegistry.shared()
    registry.register_builtin()
    runtime = registry.get(rid)
    name = runtime.resource_name(
        pid,
        prefix=ProjectSandbox.shared().prefix(),
        shared=False,
    )
    if _dramatiq_enabled():
        cleanup_project_sandbox.send(pid, rid)
        return {
            "action": "queued",
            "name": name,
            "project_id": pid,
            "runtime_id": rid,
        }
    return ProjectSandbox.remove(pid, runtime_id=rid)


@dramatiq.actor(queue_name="peon.jobs", max_retries=3, time_limit=120_000)
def cleanup_learn_lab() -> None:
    """Remove the Learn install-test container on the worker.

    Same rationale as project sandbox cleanup: ``docker rm`` on web can drop
    the browser connection mid-request.
    """
    close_old_connections()
    try:
        result = LearnLab.shared().delete()
        logger.info("cleanup_learn_lab → %s", result)
    except Exception:
        logger.exception("cleanup_learn_lab failed")
    finally:
        close_old_connections()


def enqueue_learn_lab_cleanup() -> dict:
    """Queue Learn-lab removal on the worker, or run inline when Dramatiq is off."""
    lab = LearnLab.shared()
    name = lab.container_name()
    if _dramatiq_enabled():
        cleanup_learn_lab.send()
        return {
            "ok": True,
            "action": "queued",
            "removed": False,
            "name": name,
            "lab": lab.status(),
        }
    result = lab.delete()
    result["action"] = "removed" if result.get("removed") else result.get("reason") or "missing"
    result["lab"] = lab.status()
    return result


# Self re-arming scanners. Exactly one chain per scanner: ``_arm`` stores a
# token in Redis and a run only re-arms while it still holds that token, so a
# second chain (several processes booting at once) retires itself, and a dead
# chain is restarted by the next process boot once its key expires.
_DURABLE_SCHEDULER_MS = max(
    15_000, int(getattr(settings, "DURABLE_SCHEDULER_INTERVAL_MS", 60_000) or 60_000)
)
_SCHEDULER_KEY = "peon:scheduler:{}"


def _redis():
    client = getattr(dramatiq.get_broker(), "client", None)
    if client is None:
        raise RuntimeError("scheduler needs the Redis broker")
    return client


def _arm(actor, name: str, delay_ms: int, *, force: bool = False) -> bool:
    """Start a chain unless one is alive. Returns True when this call armed it."""
    import uuid

    token = uuid.uuid4().hex
    ttl = max(60, (delay_ms * 3) // 1000 + 120)
    key = _SCHEDULER_KEY.format(name)
    if not _redis().set(key, token, nx=not force, ex=ttl):
        return False
    actor.send_with_options(args=(token,), delay=delay_ms)
    return True


def _rearm(actor, name: str, token: str, delay_ms: int) -> None:
    """Re-queue the chain that owns ``token``; a stale chain stops here."""
    if not token:
        # Legacy untokened message: hand over to a tokened chain (if none runs).
        _arm(actor, name, delay_ms)
        return
    key = _SCHEDULER_KEY.format(name)
    client = _redis()
    current = client.get(key)
    current = current.decode() if isinstance(current, bytes) else current
    if current not in (None, token):
        logger.info("%s: duplicate chain retired", name)
        return
    client.set(key, token, ex=max(60, (delay_ms * 3) // 1000 + 120))
    actor.send_with_options(args=(token,), delay=delay_ms)


@dramatiq.actor(queue_name="peon.jobs", max_retries=1, time_limit=120_000)
def durable_scheduler(token: str = "") -> None:
    """Fire due ProjectSchedule rows (cron/interval), then re-queue self."""
    close_old_connections()
    try:
        from peon.projects.schedules import fire_due_schedules

        result = fire_due_schedules()
        if result.get("fired"):
            logger.info("durable_scheduler fired %s job(s)", len(result["fired"]))
    except Exception:
        logger.exception("durable_scheduler failed")
    finally:
        close_old_connections()
        if _dramatiq_enabled():
            try:
                _rearm(durable_scheduler, "durable", token, _DURABLE_SCHEDULER_MS)
            except Exception:
                logger.warning("durable_scheduler re-arm failed", exc_info=True)


def ensure_durable_scheduler(*, force: bool = False) -> bool:
    """Start the durable schedule scanner if no chain is alive (``force``: always)."""
    if not _dramatiq_enabled():
        return False
    if not getattr(settings, "DURABLE_SCHEDULER_ENABLED", True):
        return False
    try:
        return _arm(durable_scheduler, "durable", 5_000, force=force)
    except Exception:
        logger.warning("ensure_durable_scheduler failed", exc_info=True)
        return False


def _dream_interval_ms() -> int:
    """Dramatiq delay (ms) from Peon Settings interval in seconds."""
    try:
        from peon.projects.runtime_settings import PeonSettings

        seconds = max(60, int(PeonSettings.get_int("PEON_DREAMING_INTERVAL_S", 900)))
    except Exception:
        seconds = max(60, int(getattr(settings, "PEON_DREAMING_INTERVAL_S", 900) or 900))
    return seconds * 1000


def _dreaming_enabled() -> bool:
    try:
        from peon.projects.runtime_settings import PeonSettings

        return PeonSettings.get_bool("PEON_DREAMING_ENABLED", True) and PeonSettings.get_bool(
            "COGNEE_ENABLED", True
        )
    except Exception:
        return bool(getattr(settings, "PEON_DREAMING_ENABLED", True)) and bool(
            getattr(settings, "COGNEE_ENABLED", True)
        )


@dramatiq.actor(queue_name="peon.jobs", max_retries=1, time_limit=600_000)
def dream_scheduler(token: str = "") -> None:
    """Run a Cognee improve (dream) cycle, then re-queue self."""
    close_old_connections()
    try:
        from orchestrator.crew.dreaming import DreamingService

        result = DreamingService.shared().run_cycle()
        if result.get("count"):
            logger.info("dream_scheduler improved %s session(s)", result.get("count"))
    except Exception:
        logger.exception("dream_scheduler failed")
    finally:
        close_old_connections()
        if _dramatiq_enabled() and _dreaming_enabled():
            try:
                _rearm(dream_scheduler, "dream", token, _dream_interval_ms())
            except Exception:
                logger.warning("dream_scheduler re-arm failed", exc_info=True)


def ensure_dream_scheduler(*, force: bool = False) -> bool:
    """Start the Cognee dream scanner if no chain is alive (``force``: always)."""
    if not _dramatiq_enabled():
        return False
    if not _dreaming_enabled():
        return False
    try:
        return _arm(dream_scheduler, "dream", 30_000, force=force)
    except Exception:
        logger.warning("ensure_dream_scheduler failed", exc_info=True)
        return False
