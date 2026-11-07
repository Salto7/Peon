"""Dramatiq actors for long-running project jobs (sandbox execution).

Ensure Django + RedisBroker are configured before @actor registration so the
worker does not fall back to dramatiq's localhost Redis default.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

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

from agent_runtime.registry import SandboxDriverRegistry
from peon.projects.job_claim import job_slots_available, mark_job_running
from peon.projects.lab_service import LabService
from peon.projects.models import Job, JobStatus, SandboxRuntime, ToolTestRun
from peon.projects.sandbox import ProjectSandbox

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


_SANDBOX_CLEANUP_DELAY_MS = 5_000


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
    registry = SandboxDriverRegistry.shared()
    registry.register_builtin()
    runtime = registry.get(rid)
    name = runtime.resource_name(
        pid,
        prefix=ProjectSandbox.shared().prefix(),
        shared=False,
    )
    if _dramatiq_enabled():
        # Delay so the web response + redirect page-load finish before Docker's
        # interfaces flap (otherwise Chrome aborts with ERR_NETWORK_CHANGED).
        cleanup_project_sandbox.send_with_options(args=(pid, rid), delay=_SANDBOX_CLEANUP_DELAY_MS)
        return {
            "action": "queued",
            "name": name,
            "project_id": pid,
            "runtime_id": rid,
        }
    return ProjectSandbox.remove(pid, runtime_id=rid)


def _run_lab_action(label: str, method: str, force: bool) -> None:
    """Shared body for the lab lifecycle actors below: run, log, never raise.

    ``docker rm``/recreate on the worker can flap Docker's network, so these
    always run off the web request (same rationale as project sandbox
    cleanup) — the three actors differ only in which ``LabService`` method
    they call.
    """
    close_old_connections()
    try:
        result = getattr(LabService.shared(), method)(force=force)
        logger.info("%s(force=%s) → %s", label, force, result)
    except Exception:
        logger.exception("%s failed", label)
    finally:
        close_old_connections()


@dramatiq.actor(queue_name="peon.jobs", max_retries=3, time_limit=120_000)
def decommission_lab(force: bool = False) -> None:
    """Remove the lab container on the worker."""
    _run_lab_action("decommission_lab", "decommission", force)


@dramatiq.actor(queue_name="peon.jobs", max_retries=3, time_limit=120_000)
def reset_lab(force: bool = False) -> None:
    """Decommission then set up fresh, on the worker."""
    _run_lab_action("reset_lab", "reset", force)


@dramatiq.actor(queue_name="peon.jobs", max_retries=3, time_limit=120_000)
def reboot_lab(force: bool = False) -> None:
    """Restart the lab container in place, on the worker."""
    _run_lab_action("reboot_lab", "reboot", force)


def _enqueue_lab_action(actor, inline, *, force: bool) -> dict:
    """Queue a lab lifecycle action on the worker, or run it inline when Dramatiq is off.

    A queued action's lock check happens later, inside the worker — too
    late to tell the operator "busy" synchronously. Probe the lock here
    first (acquire-and-release, not held across the real queued run) so a
    busy lab is reported immediately instead of as a silent worker-log
    failure minutes later; ``force`` skips the probe same as it skips the
    real lock.
    """
    lab = LabService.shared()
    name = lab.container_name()
    if not force:
        try:
            with lab._exclusive():
                pass
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc), "name": name, "lab": lab.status()}
    if _dramatiq_enabled():
        actor.send(force)
        return {"ok": True, "action": "queued", "name": name, "lab": lab.status()}
    result = inline(force=force)
    result.setdefault("action", "removed" if result.get("removed") else result.get("reason") or "missing")
    result["lab"] = lab.status()
    return result


def enqueue_lab_decommission(*, force: bool = False) -> dict:
    return _enqueue_lab_action(decommission_lab, LabService.shared().decommission, force=force)


def enqueue_lab_reset(*, force: bool = False) -> dict:
    return _enqueue_lab_action(reset_lab, LabService.shared().reset, force=force)


def enqueue_lab_reboot(*, force: bool = False) -> dict:
    return _enqueue_lab_action(reboot_lab, LabService.shared().reboot, force=force)


@dramatiq.actor(queue_name="peon.jobs", max_retries=0, time_limit=900_000)
def run_tool_test(run_id: str, yaml_text: str, install_script: str) -> None:
    """Run one install test on the worker (same docker-flap rationale as the
    lab lifecycle actors — ``test_tool_install`` starts with the same
    ``_ensure_clean`` destroy+recreate). ``max_retries=0``: a failed run
    should report back as a failure, not silently retry a destructive
    docker operation the browser already stopped waiting on.
    """
    close_old_connections()
    try:
        result = LabService.shared().test_tool_install(
            yaml_text=yaml_text, install_script=install_script
        )
    except Exception as exc:  # noqa: BLE001 — reported to the poller, not raised
        logger.exception("run_tool_test failed")
        result = {
            "ok": False,
            "verified": False,
            "message": str(exc),
            "log": "",
            "lab": LabService.shared().status(),
            "tool_id": "",
        }
    ToolTestRun.objects.filter(pk=run_id).update(status="done", result=result)
    close_old_connections()


def enqueue_tool_test(*, yaml_text: str, install_script: str) -> dict:
    """Start one install test; the caller polls ``ToolTestRun`` for the result.

    No ``force`` here — test installs stay strictly mutex'd, unlike the
    lifecycle actions (an operator-confirmed override makes sense for "this
    lab seems stuck"; it does not for "let two installs race").
    """
    lab = LabService.shared()
    try:
        with lab._exclusive():
            pass
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc), "lab": lab.status()}

    run = ToolTestRun.objects.create(tool_id="")
    if _dramatiq_enabled():
        run_tool_test.send(str(run.pk), yaml_text, install_script)
    else:
        result = LabService.shared().test_tool_install(
            yaml_text=yaml_text, install_script=install_script
        )
        run.status = "done"
        run.result = result
        run.save(update_fields=["status", "result"])
    return {"ok": True, "run_id": str(run.pk)}


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


def _run_scheduler_tick(
    *,
    actor,
    key: str,
    token: str,
    delay_ms: int,
    label: str,
    work: Callable[[], None],
    rearm_enabled: Callable[[], bool] = _dramatiq_enabled,
) -> None:
    """Shared body for a self-requeuing periodic actor: run ``work``, then re-arm.

    Every scheduler in this module (durable/dream/child-timeout) is "do one
    sweep, log if it did anything, re-queue self" with the same connection
    cleanup and error handling either side — this is that skeleton, written
    once.
    """
    close_old_connections()
    try:
        work()
    except Exception:
        logger.exception("%s failed", label)
    finally:
        close_old_connections()
        if rearm_enabled():
            try:
                _rearm(actor, key, token, delay_ms)
            except Exception:
                logger.warning("%s re-arm failed", label, exc_info=True)


def _ensure_scheduler(
    actor,
    key: str,
    initial_delay_ms: int,
    *,
    label: str,
    enabled: Callable[[], bool] = lambda: True,
    force: bool = False,
) -> bool:
    """Shared body for an ``ensure_*_scheduler`` boot hook."""
    if not _dramatiq_enabled():
        return False
    if not enabled():
        return False
    try:
        return _arm(actor, key, initial_delay_ms, force=force)
    except Exception:
        logger.warning("%s failed", label, exc_info=True)
        return False


@dramatiq.actor(queue_name="peon.jobs", max_retries=1, time_limit=120_000)
def durable_scheduler(token: str = "") -> None:
    """Fire due ProjectSchedule rows (cron/interval), then re-queue self."""

    def work() -> None:
        from peon.projects.schedules import fire_due_schedules

        result = fire_due_schedules()
        if result.get("fired"):
            logger.info("durable_scheduler fired %s job(s)", len(result["fired"]))

    _run_scheduler_tick(
        actor=durable_scheduler,
        key="durable",
        token=token,
        delay_ms=_DURABLE_SCHEDULER_MS,
        label="durable_scheduler",
        work=work,
    )


def ensure_durable_scheduler(*, force: bool = False) -> bool:
    """Start the durable schedule scanner if no chain is alive (``force``: always)."""
    return _ensure_scheduler(
        durable_scheduler,
        "durable",
        5_000,
        label="ensure_durable_scheduler",
        enabled=lambda: getattr(settings, "DURABLE_SCHEDULER_ENABLED", True),
        force=force,
    )


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

    def work() -> None:
        from orchestrator.crew.dreaming import DreamingService

        result = DreamingService.shared().run_cycle()
        if result.get("count"):
            logger.info("dream_scheduler improved %s session(s)", result.get("count"))

    _run_scheduler_tick(
        actor=dream_scheduler,
        key="dream",
        token=token,
        delay_ms=_dream_interval_ms(),
        label="dream_scheduler",
        work=work,
        rearm_enabled=lambda: _dramatiq_enabled() and _dreaming_enabled(),
    )


def ensure_dream_scheduler(*, force: bool = False) -> bool:
    """Start the Cognee dream scanner if no chain is alive (``force``: always)."""
    return _ensure_scheduler(
        dream_scheduler,
        "dream",
        30_000,
        label="ensure_dream_scheduler",
        enabled=_dreaming_enabled,
        force=force,
    )


@dramatiq.actor(queue_name="peon.jobs", max_retries=3, time_limit=600_000)
def resweep_catalog_bindings() -> None:
    """Re-resolve any skill/MCP currently unbound from any role.

    Enqueued whenever a role is created or edited (``PlatformCatalog.
    upsert_role``, deferred to that transaction's commit) — a role change may
    be exactly what a previously-orphaned item needed. Cheap when there is
    nothing to do: one anti-join query, nothing else.
    """
    close_old_connections()
    try:
        from peon.projects.platform_catalog import PlatformCatalog

        result = PlatformCatalog.shared().resweep_unbound_owners()
        if result.get("resolved") or result.get("remaining"):
            logger.info("resweep_catalog_bindings -> %s", result)
    except Exception:
        logger.exception("resweep_catalog_bindings failed")
    finally:
        close_old_connections()


def enqueue_catalog_resweep() -> dict:
    """Queue a catalog resweep on the worker, or run it inline when Dramatiq is off."""
    if _dramatiq_enabled():
        resweep_catalog_bindings.send()
        return {"ok": True, "action": "queued"}
    from peon.projects.platform_catalog import PlatformCatalog

    return {"ok": True, **PlatformCatalog.shared().resweep_unbound_owners()}


_CHILD_TIMEOUT_WATCHDOG_MS = 300_000  # 5 min; AGENT_CHILD_TIMEOUT_SEC sets the budget itself


@dramatiq.actor(queue_name="peon.jobs", max_retries=1, time_limit=120_000)
def child_timeout_watchdog(token: str = "") -> None:
    """Flag spawned children over budget (Core 1.4), then re-queue self."""

    def work() -> None:
        from peon.projects.a2a import sweep_child_timeouts

        result = sweep_child_timeouts()
        if result.get("flagged"):
            logger.info("child_timeout_watchdog flagged %s job(s)", len(result["flagged"]))

    _run_scheduler_tick(
        actor=child_timeout_watchdog,
        key="child_timeout",
        token=token,
        delay_ms=_CHILD_TIMEOUT_WATCHDOG_MS,
        label="child_timeout_watchdog",
        work=work,
    )


def ensure_child_timeout_watchdog(*, force: bool = False) -> bool:
    """Start the child-timeout sweep if no chain is alive (``force``: always)."""
    return _ensure_scheduler(
        child_timeout_watchdog, "child_timeout", 10_000, label="ensure_child_timeout_watchdog", force=force
    )


_PROJECT_BUDGET_WATCHDOG_MS = 900_000  # 15 min; AGENT_PROJECT_BUDGET_USD sets the budget itself


@dramatiq.actor(queue_name="peon.jobs", max_retries=1, time_limit=120_000)
def project_budget_watchdog(token: str = "") -> None:
    """Flag projects over their configured LLM spend (Core 2.5), then re-queue self."""

    def work() -> None:
        from peon.projects.lifecycle import sweep_project_budgets

        result = sweep_project_budgets()
        if result.get("flagged"):
            logger.info("project_budget_watchdog flagged %s project(s)", len(result["flagged"]))

    _run_scheduler_tick(
        actor=project_budget_watchdog,
        key="project_budget",
        token=token,
        delay_ms=_PROJECT_BUDGET_WATCHDOG_MS,
        label="project_budget_watchdog",
        work=work,
    )


def ensure_project_budget_watchdog(*, force: bool = False) -> bool:
    """Start the project-budget sweep if no chain is alive (``force``: always)."""
    return _ensure_scheduler(
        project_budget_watchdog,
        "project_budget",
        10_000,
        label="ensure_project_budget_watchdog",
        force=force,
    )


@dramatiq.actor(queue_name="peon.jobs", max_retries=1, time_limit=120_000)
def classify_roe_candidates(project_id: str, values: list[str]) -> None:
    """Background Core 1.5 soft scope hint pass — never runs on the finding path."""
    close_old_connections()
    try:
        from peon.projects.runtime_settings import PeonSettings

        if not PeonSettings.get_bool("ROE_CANDIDATE_CLASSIFIER_ENABLED", True):
            return
        from peon.projects.roe_ops import annotate_candidate_scope_hints

        n = annotate_candidate_scope_hints(project_id, values)
        if n:
            logger.info("classify_roe_candidates annotated %s candidate(s)", n)
    except Exception:
        logger.exception("classify_roe_candidates failed")
    finally:
        close_old_connections()


def enqueue_candidate_classification(project_id: str, values: list[str]) -> dict:
    """Queue a candidate scope-hint pass, or run it inline when Dramatiq is off."""
    if not values:
        return {"ok": True, "action": "skipped"}
    if _dramatiq_enabled():
        classify_roe_candidates.send(project_id, values)
        return {"ok": True, "action": "queued"}
    from peon.projects.runtime_settings import PeonSettings

    if not PeonSettings.get_bool("ROE_CANDIDATE_CLASSIFIER_ENABLED", True):
        return {"ok": True, "action": "disabled"}
    from peon.projects.roe_ops import annotate_candidate_scope_hints

    return {"ok": True, "annotated": annotate_candidate_scope_hints(project_id, values)}


@dramatiq.actor(queue_name="peon.jobs", max_retries=1, time_limit=30_000)
def send_notification_task(subject: str, text: str, extra: dict | None = None) -> None:
    """Fan out one notification on the worker (Core 5.1) — HTTP/SMTP never on the web request."""
    close_old_connections()
    try:
        from peon.projects.notifications import notify

        result = notify(subject, text, extra=extra)
        if any(result.values()):
            logger.info("send_notification_task sent via %s", [k for k, v in result.items() if v])
    except Exception:
        logger.exception("send_notification_task failed")
    finally:
        close_old_connections()


def enqueue_notification(subject: str, text: str, *, extra: dict | None = None) -> dict:
    """Queue a notification fan-out, or run it inline when Dramatiq is off."""
    if _dramatiq_enabled():
        send_notification_task.send(subject, text, extra)
        return {"ok": True, "action": "queued"}
    from peon.projects.notifications import notify

    return {"ok": True, "sent": notify(subject, text, extra=extra)}
