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

from peon.projects.models import TERMINAL_JOB_STATUSES, Job, JobStatus
from peon.projects.sandbox import ProjectSandbox
from peon.projects.worker import _finish, mark_job_running, run_job

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="peon.jobs", max_retries=3, time_limit=6 * 60 * 60 * 1000)
def process_job(job_id: str) -> None:
    """Claim PENDING→RUNNING then run skills inside the project sandbox."""
    close_old_connections()

    try:
        job = Job.objects.select_related("project").get(pk=job_id)
    except Job.DoesNotExist:
        logger.warning("process_job: missing job %s", job_id)
        return

    if job.status in TERMINAL_JOB_STATUSES:
        logger.info("process_job: skip terminal job %s (%s)", job_id, job.status)
        return

    if job.status == JobStatus.PENDING:
        from peon.projects.worker import job_slots_available

        if not job_slots_available(job):
            # Soft back-pressure: leave PENDING and retry shortly.
            try:
                process_job.send_with_options(args=(str(job_id),), delay=5_000)
            except Exception:
                logger.info(
                    "process_job: slots full for %s; leave pending (no delay requeue)",
                    job_id,
                )
            return
        if mark_job_running(job) is None:
            job.refresh_from_db()
    elif job.status == JobStatus.PAUSED:
        logger.info("process_job: skip paused job %s", job_id)
        return

    try:
        run_job(job)
    except Exception as exc:
        logger.exception("process_job failed for %s", job_id)
        _finish(job, status=JobStatus.FAILED, error=str(exc))
    finally:
        close_old_connections()


def enqueue_job(job_id: str) -> bool:
    """Enqueue a job for Dramatiq when enabled; return False if caller should poll-run."""
    if not getattr(settings, "DRAMATIQ_ENABLED", True):
        return False
    process_job.send(str(job_id))
    return True


@dramatiq.actor(queue_name="peon.jobs", max_retries=3, time_limit=120_000)
def cleanup_project_sandbox(project_id: str) -> None:
    """Remove a project sandbox container on the worker (has docker.sock).

    Running ``docker rm`` inside the web request can flap Docker's network and
    interrupt the browser mid-redirect — so delete enqueues this instead.
    """
    close_old_connections()
    try:
        result = ProjectSandbox.remove(str(project_id))
        logger.info("cleanup_project_sandbox %s → %s", project_id, result)
    except Exception:
        logger.exception("cleanup_project_sandbox failed for %s", project_id)
    finally:
        close_old_connections()


def enqueue_sandbox_cleanup(project_id: str) -> dict:
    """Queue sandbox removal on the worker, or run inline when Dramatiq is off."""
    pid = str(project_id or "").strip()
    if not pid:
        return {"action": "skipped", "reason": "no project_id"}
    name = ProjectSandbox.container_name(pid)
    if getattr(settings, "DRAMATIQ_ENABLED", True):
        cleanup_project_sandbox.send(pid)
        return {"action": "queued", "name": name, "project_id": pid}
    return ProjectSandbox.remove(pid)


@dramatiq.actor(queue_name="peon.jobs", max_retries=3, time_limit=120_000)
def cleanup_learn_lab() -> None:
    """Remove the Learn install-test container on the worker.

    Same rationale as project sandbox cleanup: ``docker rm`` on web can drop
    the browser connection mid-request.
    """
    close_old_connections()
    try:
        from peon.projects.learn_lab import LearnLab

        result = LearnLab.shared().delete()
        logger.info("cleanup_learn_lab → %s", result)
    except Exception:
        logger.exception("cleanup_learn_lab failed")
    finally:
        close_old_connections()


def enqueue_learn_lab_cleanup() -> dict:
    """Queue Learn-lab removal on the worker, or run inline when Dramatiq is off."""
    from peon.projects.learn_lab import LearnLab

    lab = LearnLab.shared()
    name = lab.container_name()
    if getattr(settings, "DRAMATIQ_ENABLED", True):
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
