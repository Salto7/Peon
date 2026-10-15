"""Objective scheduling — Peontester-style per-objective readiness."""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone as dj_tz

from peon.projects.models import (
    FINISHED_PROJECT_STATUSES,
    INACTIVE_PROJECT_STATUSES,
    Job,
    JobLifecycle,
    JobStatus,
    KillChainPhase,
    Objective,
    ObjectiveStatus,
    Project,
    ProjectKind,
    ProjectStatus,
)
from orchestrator.utils.commands import normalize_objective_commands
from orchestrator.utils.paths import format_roe_block
from peon.projects.tasks import enqueue_job
from peon.projects.workspaces import project_workspace_dir, safe_workspace_key

logger = logging.getLogger(__name__)


def _project_alias(project_id) -> str:
    from peon.projects.db import ensure_project_database

    return ensure_project_database(project_id)


def inherited_objective_dependencies(
    parent: Objective | None,
    additional: list[Objective] | tuple[Objective, ...] = (),
) -> list[Objective]:
    """Parent gate + its direct gates + child-specific gates, de-duplicated."""
    ordered: list[Objective] = []
    if parent is not None:
        ordered.extend(parent.depends_on.order_by("seq", "created_at"))
        ordered.append(parent)
    ordered.extend(additional)
    seen: set[object] = set()
    out: list[Objective] = []
    for objective in ordered:
        if objective.pk is None or objective.pk in seen:
            continue
        seen.add(objective.pk)
        out.append(objective)
    return out


class ObjectiveScheduler:
    """Gate and enqueue Jobs for ready objectives (deps + caps, not serial stall)."""

    def dependencies_met(self, objective: Objective) -> bool:
        alias = _project_alias(objective.project_id)
        objective = (
            Objective.objects.using(alias)
            .prefetch_related("depends_on")
            .get(pk=objective.pk)
        )
        deps = objective.depends_on.all()
        if not deps.exists():
            return True
        return all(d.status == ObjectiveStatus.COMPLETED for d in deps)

    def active_jobs(self, objective: Objective):
        """Jobs still capable of producing work for this objective."""
        return objective.jobs.exclude(
            status__in={
                JobStatus.COMPLETED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            }
        )

    def all_jobs_terminal(self, objective: Objective) -> bool:
        """True only when every Job tagged to the objective has finished."""
        return not self.active_jobs(objective).exists()

    def settle(self, objective: Objective) -> Objective:
        """Advance status after all tagged jobs finish. Leaves BLOCKED alone.

        Outcome follows the newest terminal job so a replan attempt is not
        poisoned by older CANCELLED/FAILED rows still tagged to the objective.
        """
        if objective.status in {
            ObjectiveStatus.COMPLETED,
            ObjectiveStatus.CANCELLED,
            ObjectiveStatus.BLOCKED,
        }:
            return objective
        if not self.all_jobs_terminal(objective):
            return objective
        latest = objective.jobs.order_by("-created_at", "-completed_at").first()
        if latest is None:
            return self.mark(objective, ObjectiveStatus.COMPLETED)
        if latest.status == JobStatus.FAILED:
            return self.mark(
                objective,
                ObjectiveStatus.BLOCKED,
                reason=(latest.error or "One or more objective jobs failed")[:2000],
            )
        if latest.status == JobStatus.CANCELLED:
            return self.mark(objective, ObjectiveStatus.CANCELLED)
        return self.mark(objective, ObjectiveStatus.COMPLETED)

    def resume_blocked(
        self, objective: Objective, *, reason: str = "Operator replied"
    ) -> Objective:
        """Move BLOCKED → IN_PROGRESS so follow-up work can run."""
        if objective.status != ObjectiveStatus.BLOCKED:
            return objective
        return self.mark(objective, ObjectiveStatus.IN_PROGRESS, reason=reason)

    def ready_objectives(self, project: Project | None) -> list[Objective]:
        """All objectives that may receive a Job now.

        Includes PENDING rows with deps met, plus orphaned IN_PROGRESS (no active
        Job). Does **not** stall when other objectives are already running —
        concurrency is limited by claim slots (``MAX_AGENTS_PER_PROJECT``).
        """
        if project is None:
            return []
        out: list[Objective] = []
        for obj in project.objectives.order_by("seq", "created_at"):
            if obj.status == ObjectiveStatus.IN_PROGRESS:
                if self.has_active_job(obj):
                    continue
                # Orphaned release — fall through and (re)enqueue.
            elif obj.status != ObjectiveStatus.PENDING:
                continue
            if not self.dependencies_met(obj):
                continue
            if not (obj.role_id or "").strip():
                self.mark(
                    obj,
                    ObjectiveStatus.BLOCKED,
                    reason="No role_id — assign a CrewAI role or replan",
                )
                continue
            out.append(obj)
        return out

    def next_ready(self, project: Project | None) -> Objective | None:
        """First ready objective (compat); prefer ``ready_objectives`` / ``enqueue_ready``."""
        ready = self.ready_objectives(project)
        return ready[0] if ready else None

    def mark(
        self,
        objective: Objective,
        status: str,
        *,
        reason: str = "",
    ) -> Objective:
        from peon.projects.db import using_project

        supplied = objective
        alias = _project_alias(objective.project_id)
        with using_project(objective.project_id):
            with transaction.atomic(using=alias):
                return self._mark_locked(
                    supplied, objective.pk, alias, status, reason=reason
                )

    def _mark_locked(
        self,
        supplied: Objective,
        objective_pk,
        alias: str,
        status: str,
        *,
        reason: str = "",
    ) -> Objective:
        objective = (
            Objective.objects.using(alias).select_for_update().get(pk=objective_pk)
        )

        def _sync_supplied(*fields: str) -> Objective:
            for field in fields:
                setattr(supplied, field, getattr(objective, field))
            return supplied

        if (
            status == ObjectiveStatus.COMPLETED
            and not self.all_jobs_terminal(objective)
        ):
            return _sync_supplied("status", "blocked_reason", "completed_at")
        if (
            status == ObjectiveStatus.IN_PROGRESS
            and objective.status == ObjectiveStatus.COMPLETED
        ):
            return _sync_supplied("status", "blocked_reason", "completed_at")
        now = dj_tz.now()
        fields = ["status", "updated_at"]
        objective.status = status
        if status == ObjectiveStatus.IN_PROGRESS:
            if objective.started_at is None:
                objective.started_at = now
                fields.append("started_at")
            objective.blocked_reason = ""
            fields.append("blocked_reason")
        elif status == ObjectiveStatus.COMPLETED:
            objective.blocked_reason = ""
            fields.append("blocked_reason")
            objective.completed_at = now
            fields.append("completed_at")
            if objective.started_at is None:
                objective.started_at = now
                fields.append("started_at")
        elif status == ObjectiveStatus.BLOCKED:
            objective.blocked_reason = (reason or "").strip()[:2000]
            fields.append("blocked_reason")
        elif status == ObjectiveStatus.CANCELLED:
            objective.blocked_reason = (reason or objective.blocked_reason or "").strip()[
                :2000
            ]
            fields.append("blocked_reason")
        objective.save(update_fields=fields)
        if status != ObjectiveStatus.BLOCKED:
            self._resolve_blocked_prompts(objective, status=status)
        return _sync_supplied(*(field for field in fields if field != "updated_at"))

    def _resolve_blocked_prompts(self, objective: Objective, *, status: str) -> None:
        """Close open blocked_objective HITL rows once the objective leaves BLOCKED."""
        from django.db.models import Q

        from peon.projects.models import OperatorPrompt, OperatorPromptKind

        now = dj_tz.now()
        note = f"auto-resolved: objective moved to {status}"
        prefix = f"OBJ-{objective.seq} "
        alias = _project_alias(objective.project_id)
        OperatorPrompt.objects.using(alias).filter(
            project_id=objective.project_id,
            kind=OperatorPromptKind.BLOCKED_OBJECTIVE,
            resolved_at__isnull=True,
        ).filter(
            Q(job__objective_id=objective.id) | Q(question__startswith=prefix)
        ).update(resolved_at=now, reply=note)

    def roles_for(self, objective: Objective) -> list[str]:
        """Primary role id for the first Job (single id; not a peer list)."""
        hint = (objective.role_id or "").strip()
        if not hint:
            return []
        # If planners historically comma-joined, take the first as primary only.
        primary = hint.replace(";", ",").split(",")[0].strip()
        return [primary] if primary else []

    def build_brief(self, project: Project, objective: Objective) -> str:
        from peon.projects.workspaces import brief_artifact_section

        parts = [
            f"Execute project objective OBJ-{objective.seq}: {objective.title}",
            f"Phase: {objective.phase}",
            f"Description: {objective.description or '(none)'}",
            f"Acceptance criteria: {objective.acceptance_criteria or '(none)'}",
            "",
            f"Project: {project.title}",
            format_roe_block(getattr(project, "roe", None)),
            (
                "Type labels (ip:, file:, malware:, …) are hints only — roles interpret "
                "values. Discoveries go to candidates/findings — not authorized until "
                "promoted into in-scope. record_finding is for engagement discoveries "
                "about subjects (any asset class) with evidence — not job/objective/"
                "agent progress. Write evidence under workspace/; curated notes under "
                "findings/<role-id>.md. Do not write findings/report.md unless this "
                "objective is the analyzer."
            ),
        ]
        section = brief_artifact_section(
            str(project.id), role_id=objective.role_id or ""
        )
        if section:
            parts.extend(["", section])
        return "\n".join(parts)

    def has_active_job(self, objective: Objective) -> bool:
        return self.active_jobs(objective).exists()

    def create_run(
        self,
        project: Project,
        objective: Objective,
        *,
        plan_text: str = "",
        plan_path: str = "",
        workspace_id: str | None = None,
        enqueue: bool = True,
        role_ids: list[str] | None = None,
        standby: bool = False,
    ) -> Job | None:
        """Spawn one Solo Job for an objective. No-op if deps unmet or job already active.

        ``standby=True`` also creates the agent when its dependencies are not
        met: the job is born ``IDLE`` (never queued) and is woken by an A2A
        handoff when the dependencies complete.
        """
        from peon.projects.db import using_project

        del workspace_id  # project identity is the only workspace identity
        alias = _project_alias(project.id)
        with using_project(project.id):
            with transaction.atomic(using=alias):
                objective = (
                    Objective.objects.using(alias)
                    .select_for_update()
                    .get(pk=objective.pk)
                )
                job, created = self._create_run_locked(
                    project,
                    objective,
                    alias=alias,
                    plan_text=plan_text,
                    plan_path=plan_path,
                    role_ids=role_ids,
                    standby=standby,
                )
        if job is None:
            return None
        if job.status == JobStatus.IDLE:
            enqueue = False  # standby agents are woken, not queued
        # Dramatiq + Cognee only after commit, and only for newly created Jobs.
        # Re-enqueueing an already-active Job was a regression from moving
        # enqueue outside the atomic.
        if created and enqueue:
            enqueue_job(str(job.id))
        if created:
            try:
                from orchestrator.skills.role_memory import upsert_role_skills

                solo = [
                    str(r).strip() for r in (job.role_ids or []) if str(r).strip()
                ][:1]
                skills = [
                    str(s).strip() for s in (job.skill_ids or []) if str(s).strip()
                ]
                if solo:
                    upsert_role_skills(
                        solo[0],
                        skills,
                        project_id=str(project.id),
                        source="objective_run",
                    )
            except Exception as exc:
                logger.warning("upsert_role_skills failed: %s", exc, exc_info=True)
        return job

    def _create_run_locked(
        self,
        project: Project,
        objective: Objective,
        *,
        alias: str,
        plan_text: str = "",
        plan_path: str = "",
        role_ids: list[str] | None = None,
        standby: bool = False,
    ) -> tuple[Job | None, bool]:
        """Return ``(job, created)``. ``created`` is False for existing active jobs."""
        if objective.status in {
            ObjectiveStatus.COMPLETED,
            ObjectiveStatus.CANCELLED,
        }:
            return None, False
        deps_met = self.dependencies_met(objective)
        if not deps_met and not standby:
            return None, False
        if self.has_active_job(objective):
            existing = objective.jobs.exclude(
                status__in={
                    JobStatus.COMPLETED,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                }
            ).order_by("-created_at").first()
            return existing, False

        preferred = (
            list(role_ids) if role_ids is not None else self.roles_for(objective)
        )
        solo = preferred[:1]
        if not solo:
            self._mark_locked(
                objective,
                objective.pk,
                alias,
                ObjectiveStatus.BLOCKED,
                reason=(
                    "No role_id — assign a CrewAI role (osint-expert, …) "
                    "or replan"
                ),
            )
            return None, False
        ws = safe_workspace_key(str(project.id))
        project_workspace_dir(ws)
        skills = [
            str(s).strip()
            for s in (getattr(objective, "skill_ids", None) or [])
            if str(s).strip()
        ]
        job = Job.objects.using(alias).create(
            title=f"OBJ-{objective.seq}: {objective.title}"[:255],
            description=self.build_brief(project, objective),
            lifecycle=JobLifecycle.LONG,
            status=JobStatus.PENDING if deps_met else JobStatus.IDLE,
            idle_reason="" if deps_met else "dependencies",
            role_ids=solo,
            skill_ids=skills,
            project=project,
            objective=objective,
            plan_text=plan_text or "",
            plan_path=plan_path or "plans/latest.md",
            workspace_id=ws,
        )
        if not deps_met:
            # Standby agent: the objective stays PENDING until it is woken.
            return job, True
        if objective.status != ObjectiveStatus.IN_PROGRESS:
            self._mark_locked(
                objective, objective.pk, alias, ObjectiveStatus.IN_PROGRESS
            )
        return job, True

    def enqueue_ready(self, project: Project | None) -> list[Job]:
        """Spawn every planned agent; start the ready ones, park the rest.

        * Objectives whose dependencies are met get a running job (or have
          their standby job woken with the dependency handoffs).
        * Objectives still waiting on dependencies get an ``IDLE`` standby
          agent now, so the whole crew exists from the moment the plan is set.
        Returns the jobs that were queued or woken.
        """
        if project is None:
            return []
        project.refresh_from_db()
        if project.status in INACTIVE_PROJECT_STATUSES:
            return []
        # Durable plans must be accepted before any objective Jobs start.
        if project.kind == ProjectKind.DURABLE and not project.plan_accepted:
            return []
        if project.status in FINISHED_PROJECT_STATUSES:
            project.status = ProjectStatus.ACTIVE
            project.completed_at = None
            project.save(update_fields=["status", "completed_at", "updated_at"])
        from peon.projects import a2a

        try:
            a2a.sweep_orphan_standby(project)
        except Exception as exc:  # noqa: BLE001
            logger.warning("standby sweep failed: %s", exc, exc_info=True)
        plan_text = (
            project.jobs.order_by("-created_at")
            .values_list("plan_text", flat=True)
            .first()
            or ""
        )
        started: list[Job] = []
        alias = _project_alias(project.id)
        ready = self.ready_objectives(project)
        for obj in ready:
            idle = (
                Job.objects.using(alias)
                .filter(objective=obj, status=JobStatus.IDLE, parent__isnull=True)
                .order_by("created_at")
                .first()
            )
            if idle is not None:
                a2a.deliver_handoffs(obj)
                if a2a.wake_job(idle, reason=f"OBJ-{obj.seq} dependencies completed"):
                    self.mark(obj, ObjectiveStatus.IN_PROGRESS)
                    started.append(idle)
                continue
            job = self.create_run(
                project,
                obj,
                plan_text=plan_text,
                workspace_id=str(project.id),
            )
            if job is not None:
                started.append(job)

        # Everyone else gets a standby agent so the crew is visible and wired.
        ready_ids = {o.pk for o in ready}
        for obj in project.objectives.order_by("seq", "created_at"):
            if obj.pk in ready_ids or obj.status != ObjectiveStatus.PENDING:
                continue
            if not (obj.role_id or "").strip() or self.has_active_job(obj):
                continue
            self.create_run(
                project,
                obj,
                plan_text=plan_text,
                workspace_id=str(project.id),
                standby=True,
            )
        return started

    def enqueue_next(self, project: Project | None) -> Job | None:
        """Enqueue all ready objective Jobs; return the first created (compat)."""
        jobs = self.enqueue_ready(project)
        return jobs[0] if jobs else None


def _normalize_phase(value: object) -> str:
    text = str(value or "").strip().lower()
    allowed = {c.value for c in KillChainPhase}
    return text if text in allowed else KillChainPhase.RECON


class ObjectivePlanSync:
    """Replace/update project objectives from planner JSON (Peontester sync_from_plan)."""

    def __init__(self, scheduler: ObjectiveScheduler | None = None) -> None:
        self._sched = scheduler or ObjectiveScheduler()

    def sync(
        self,
        project: Project,
        objectives_payload: list[dict],
        *,
        replace: bool = True,
    ) -> list[Objective]:
        from peon.projects.db import ensure_project_database, using_project

        alias = ensure_project_database(project.id)
        with using_project(project.id):
            with transaction.atomic(using=alias):
                return self._sync_locked(
                    project, objectives_payload, alias=alias, replace=replace
                )

    def _sync_locked(
        self,
        project: Project,
        objectives_payload: list[dict],
        *,
        alias: str,
        replace: bool = True,
    ) -> list[Objective]:
        planned = [x for x in (objectives_payload or []) if isinstance(x, dict)]
        existing = list(project.objectives.order_by("seq"))
        by_key = {(o.title.strip().lower(), o.phase): o for o in existing}
        created: list[Objective] = []
        keep_ids: set = set()
        pending_deps: list[tuple[Objective, list]] = []

        for i, item in enumerate(planned, start=1):
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            phase = _normalize_phase(item.get("phase"))
            key = (title.lower(), phase)
            cmds = normalize_objective_commands(item.get("commands"))
            mitre = item.get("mitre") or item.get("mitre_techniques") or []
            if not isinstance(mitre, list):
                mitre = []
            role_id = str(item.get("role_id") or "").strip()
            skills_raw = item.get("skill_ids") or []
            if not isinstance(skills_raw, list):
                skills_raw = []
            skill_ids = [str(s).strip() for s in skills_raw if str(s).strip()]
            profile = str(
                item.get("profile_suggestion") or item.get("profile") or ""
            ).strip()
            acceptance = str(item.get("acceptance_criteria") or "").strip()
            description = str(item.get("description") or "").strip()
            dep_refs = item.get("depends_on") or []
            if not isinstance(dep_refs, list):
                dep_refs = []

            obj = by_key.get(key)
            if obj is None:
                obj = Objective.objects.using(alias).create(
                    project=project,
                    seq=int(item.get("seq") or i),
                    title=title,
                    description=description,
                    phase=phase,
                    mitre_techniques=[str(m).strip() for m in mitre if str(m).strip()][:12],
                    acceptance_criteria=acceptance,
                    status=ObjectiveStatus.PENDING,
                    role_id=role_id,
                    skill_ids=skill_ids,
                    profile_suggestion=profile,
                    commands=cmds,
                )
            else:
                keep_ids.add(obj.id)
                obj.seq = int(item.get("seq") or i)
                obj.description = description or obj.description
                obj.acceptance_criteria = acceptance or obj.acceptance_criteria
                obj.mitre_techniques = (
                    [str(m).strip() for m in mitre if str(m).strip()][:12]
                    or obj.mitre_techniques
                )
                obj.role_id = role_id or obj.role_id
                if skill_ids:
                    obj.skill_ids = skill_ids
                obj.profile_suggestion = profile or obj.profile_suggestion
                if cmds:
                    obj.commands = cmds
                if replace and obj.status in {
                    ObjectiveStatus.COMPLETED,
                    ObjectiveStatus.CANCELLED,
                    ObjectiveStatus.BLOCKED,
                }:
                    # Replan must re-open matched terminal objectives (esp. analyzer
                    # bookend). Leaving them COMPLETED skipped the report rewrite.
                    # Detach prior-attempt jobs so settle cannot re-cancel from them.
                    Job.objects.using(alias).filter(objective=obj).update(objective=None)
                    obj.status = ObjectiveStatus.PENDING
                    obj.blocked_reason = ""
                    obj.completed_at = None
                    obj.started_at = None
                obj.save(using=alias)
            created.append(obj)
            pending_deps.append((obj, dep_refs))

        seq_map = {o.seq: o for o in created}
        # Also map by 1-based index in created list for depends_on from planner.
        for obj, refs in pending_deps:
            deps = []
            for ref in refs:
                try:
                    idx = int(ref)
                except (TypeError, ValueError):
                    continue
                dep = seq_map.get(idx)
                if dep is None and 1 <= idx <= len(created):
                    dep = created[idx - 1]
                if dep is not None and dep.id != obj.id:
                    deps.append(dep)
            obj.depends_on.set(deps)

        if replace:
            created_ids = {o.id for o in created}
            for old in existing:
                if old.id in keep_ids or old.id in created_ids:
                    continue
                if old.status == ObjectiveStatus.COMPLETED:
                    continue
                old.status = ObjectiveStatus.CANCELLED
                old.blocked_reason = "Superseded by replanned project plan"
                old.save(
                    using=alias,
                    update_fields=["status", "blocked_reason", "updated_at"],
                )

        return list(
            project.objectives.exclude(status=ObjectiveStatus.CANCELLED).order_by("seq")
        )
