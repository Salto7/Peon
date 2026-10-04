"""Persist LLM plan drafts; PlanningService composes draft + persist mixins."""

from __future__ import annotations

import uuid
from typing import Any

from django.db import transaction

from orchestrator.planning import (
    JobPlanner,
    ProjectPlanner,
    bookend_project_objectives,
    bookend_role_ids,
)
from peon.projects.models import (
    Job,
    JobLifecycle,
    JobStatus,
    Project,
    ProjectStatus,
    RulesOfEngagement,
    SandboxRuntime,
)
from peon.projects.objectives import ObjectivePlanSync, ObjectiveScheduler
from peon.projects.planning_draft import PlanningDraftMixin
from peon.projects.planning_types import (
    PlanDraft,
    PlanResult,
    validate_project_objectives,
)
from peon.projects.roe_ops import provision_project_roe
from peon.projects.target_shapes import (
    coerce_targets,
    extract_targets,
)
from peon.projects.tasks import enqueue_job
from peon.projects.workspaces import project_workspace_dir, safe_workspace_key


class PlanningPersistMixin:
    """DB + workspace persistence for plans."""

    @classmethod
    @transaction.atomic
    def persist_project_plan(cls, 
        *,
        title: str,
        summary: str,
        description: str,
        plan_text: str,
        objectives_payload: list[dict[str, Any]],
        in_scope: list | None = None,
        exclusions: list | None = None,
        authorization: str = "",
        role_ids: list[str] | None = None,
        workspace_id: str | None = None,
        project: Project | None = None,
        preferred_tags: list[str] | None = None,
        replace_objectives: bool = True,
        start: bool = True,
    ) -> PlanResult:
        """Sync objectives (replace by default) and enqueue the first ready objective job."""
        del role_ids  # per-objective roles come from each objective row
        scope = coerce_targets(in_scope or [])
        excl = coerce_targets(exclusions or [])
        tags = list(preferred_tags or [])
        auth = (authorization or "").strip()

        objectives_payload = bookend_project_objectives(list(objectives_payload or []))
        validate_project_objectives(objectives_payload)

        if project is None:
            project = Project.objects.create(
                title=(title or "adhoc").strip() or "adhoc",
                summary=(summary or "").strip(),
                status=ProjectStatus.ACTIVE,
                focus_tags=tags,
            )
            if not scope:
                scope = extract_targets(description, title, summary)
            RulesOfEngagement.objects.create(
                project=project,
                in_scope=scope,
                exclusions=excl,
                seed=(
                    [{"type": "other", "value": (summary or description or title)[:500]}]
                    if (summary or description) and not scope
                    else []
                ),
                authorization_note=auth
                or (
                    "Auto-deduced from brief (operator did not supply Rules of Engagement)."
                    if scope
                    else ""
                ),
            )
        else:
            if title:
                project.title = title.strip() or project.title
            if summary is not None:
                project.summary = summary.strip()
            if tags:
                project.focus_tags = tags
            project.status = ProjectStatus.ACTIVE
            project.completed_at = None
            project.save(
                update_fields=[
                    "title",
                    "summary",
                    "focus_tags",
                    "status",
                    "completed_at",
                    "updated_at",
                ]
            )
            if scope:
                roe, _ = RulesOfEngagement.objects.get_or_create(project=project)
                roe.in_scope = scope
                if excl:
                    roe.exclusions = excl
                if auth:
                    roe.authorization_note = auth
                roe.save()
            else:
                provision_project_roe(
                    project,
                    texts=[description, title, summary],
                    exclusions=excl or None,
                    authorization=auth,
                )

        ws = safe_workspace_key(workspace_id or str(project.id))
        project_workspace_dir(ws)
        created = ObjectivePlanSync().sync(
            project, objectives_payload, replace=replace_objectives
        )
        plan_path = ProjectPlanner().persist(
            ws, plan_text, reason="replan" if replace_objectives else "initial"
        )

        job = None
        if start:
            sched = ObjectiveScheduler()
            ready = sched.next_ready(project)
            if ready is not None:
                job = sched.create_run(
                    project,
                    ready,
                    plan_text=plan_text,
                    plan_path=plan_path or "plans/latest.md",
                    workspace_id=ws,
                    enqueue=True,
                )

        used_roles = [
            str(o.role_id).strip()
            for o in created
            if str(o.role_id or "").strip()
        ]
        return PlanResult(
            project=project,
            job=job,
            plan_text=plan_text,
            objectives=created,
            role_ids=bookend_role_ids(used_roles),
        )


    @classmethod
    @transaction.atomic
    def persist_job_plan(cls, 
        *,
        description: str,
        plan_text: str,
        title: str = "adhoc-job",
        role_ids: list[str] | None = None,
        workspace_id: str | None = None,
    ) -> PlanResult:
        """Write a standalone Job + plan file (job mode — role list, no objectives)."""
        roles = list(role_ids or [])
        ws = safe_workspace_key(workspace_id or f"job-{uuid.uuid4()}")
        project_workspace_dir(ws)

        plan_path = JobPlanner().persist(ws, plan_text, reason="initial")
        job = Job.objects.create(
            title=(title or "adhoc-job").strip() or "adhoc-job",
            description=(description or "").strip(),
            lifecycle=JobLifecycle.SHORT,
            status=JobStatus.PENDING,
            role_ids=roles,
            plan_text=plan_text,
            plan_path=plan_path or "plans/latest.md",
            workspace_id=ws,
        )
        enqueue_job(str(job.id))
        return PlanResult(job=job, plan_text=plan_text, role_ids=roles)

    @classmethod
    def persist_draft(cls, 
        draft: PlanDraft,
        *,
        workspace_id: str | None = None,
        project: Project | None = None,
    ) -> PlanResult:
        """Persist a PlanDraft to DB + disk."""
        if draft.mode == "project":
            return cls.persist_project_plan(
                title=draft.title,
                summary=draft.summary,
                description=draft.description,
                plan_text=draft.plan_text,
                objectives_payload=draft.objectives_payload,
                in_scope=draft.in_scope,
                exclusions=draft.exclusions,
                authorization=draft.authorization,
                role_ids=draft.role_ids,
                workspace_id=workspace_id,
                project=project,
                preferred_tags=draft.preferred_tags,
                replace_objectives=True,
                start=True,
            )
        return cls.persist_job_plan(
            description=draft.description,
            plan_text=draft.plan_text,
            title=draft.title,
            role_ids=draft.role_ids,
            workspace_id=workspace_id,
        )

    @classmethod
    def create_project_shell(
        cls,
        *,
        title: str,
        summary: str = "",
        in_scope: list | None = None,
        path_values: list[str] | None = None,
        operator_supplied_scope: bool = False,
        sandbox_runtime: str = "",
    ) -> tuple[Project, list, RulesOfEngagement | None]:
        """Create Project + RoE shell (before optional LLM plan).

        Returns ``(project, scope, roe)``. ``path_values`` are sandbox input
        paths folded into seed + in_scope.
        """
        title = (title or "").strip() or "untitled"
        summary = (summary or "").strip()
        scope = coerce_targets(in_scope or [])
        paths = [str(p).strip() for p in (path_values or []) if str(p).strip()]

        project = Project.objects.create(
            title=title,
            summary=summary,
            status=ProjectStatus.ACTIVE,
            sandbox_runtime=SandboxRuntime.resolve(sandbox_runtime),
        )
        if not scope:
            scope = extract_targets(title, summary)

        seed: list = []
        if summary and not scope:
            seed.append({"type": "other", "value": summary[:500]})
        for sp in paths:
            seed.append({"type": "path", "value": sp})
            scope = list(scope) + [{"type": "path", "value": sp}]

        RulesOfEngagement.objects.create(
            project=project,
            in_scope=scope,
            seed=seed,
            authorization_note=(
                "Auto-deduced from summary (operator did not supply Rules of Engagement)."
                if scope and not operator_supplied_scope
                else ""
            ),
        )
        provision_project_roe(project, texts=[title, summary])
        project.refresh_from_db()
        roe = getattr(project, "roe", None)
        final_scope = list(roe.in_scope) if roe else coerce_targets(scope)
        return project, final_scope, roe


class PlanningService(PlanningPersistMixin, PlanningDraftMixin):
    """Draft and persist project/job plans via orchestrator planners."""


__all__ = [
    "PlanDraft",
    "PlanResult",
    "PlanningPersistMixin",
    "PlanningService",
    "validate_project_objectives",
]

