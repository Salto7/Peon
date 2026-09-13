"""Thin planning services: call orchestrator planners, persist Project/Job/Objective.

Project mode schedules **one Job per ready objective** (objective-style), not a
single skill-batch job for the whole plan.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from django.db import transaction

from orchestrator.planning.job_plan import JobPlanner
from orchestrator.planning.project_plan import (
    ProjectPlanner,
    bookend_project_objectives,
    bookend_skill_names,
    parse_project_objectives,
)
from orchestrator.skills.misc.registry import SkillRegistry
from orchestrator.skills.misc.router import SkillRouter
from orchestrator.utils.llm import chat_model

from peon.projects.models import (
    Job,
    JobLifecycle,
    JobStatus,
    Project,
    ProjectStatus,
    RulesOfEngagement,
)
from peon.projects.objectives import ObjectivePlanSync, ObjectiveScheduler
from peon.projects.targets import (
    coerce_targets,
    extract_scope_assets,
    format_targets,
    provision_project_roe,
)
from peon.projects.tasks import enqueue_job
from peon.projects.workspaces import project_workspace_dir, safe_workspace_key


@dataclass
class PlanDraft:
    """LLM plan output before DB persist."""

    mode: str
    plan_text: str
    skill_names: list[str] = field(default_factory=list)
    objectives_payload: list[dict[str, Any]] = field(default_factory=list)
    description: str = ""
    title: str = "adhoc"
    summary: str = ""
    in_scope: list[dict[str, str]] = field(default_factory=list)
    exclusions: list[dict[str, str]] = field(default_factory=list)
    authorization: str = ""
    preferred_tags: list[str] = field(default_factory=list)


@dataclass
class PlanResult:
    job: Job | None
    plan_text: str
    project: Project | None = None
    objectives: list | None = None
    skill_names: list[str] | None = None



class PlanningService:
    """Draft and persist project/job plans via orchestrator planners."""

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
        skill_names: list[str] | None = None,
        workspace_id: str | None = None,
        project: Project | None = None,
        preferred_tags: list[str] | None = None,
        replace_objectives: bool = True,
        start: bool = True,
    ) -> PlanResult:
        """Sync objectives (replace by default) and enqueue the first ready objective job."""
        del skill_names  # per-objective skills come from each objective row
        scope = coerce_targets(in_scope or [])
        excl = coerce_targets(exclusions or [])
        tags = list(preferred_tags or [])
        auth = (authorization or "").strip()

        objectives_payload = bookend_project_objectives(list(objectives_payload or []))

        if project is None:
            project = Project.objects.create(
                title=(title or "adhoc").strip() or "adhoc",
                summary=(summary or "").strip(),
                status=ProjectStatus.ACTIVE,
                focus_tags=tags,
            )
            if not scope:
                scope = extract_scope_assets(description, title, summary)
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
                    "Auto-deduced from brief (operator did not supply RoE)."
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

        skills = [
            str(o.skill_suggestion).strip()
            for o in created
            if str(o.skill_suggestion or "").strip()
        ]
        return PlanResult(
            project=project,
            job=job,
            plan_text=plan_text,
            objectives=created,
            skill_names=bookend_skill_names(skills),
        )


    @classmethod
    @transaction.atomic
    def persist_job_plan(cls, 
        *,
        description: str,
        plan_text: str,
        title: str = "adhoc-job",
        skill_names: list[str] | None = None,
        workspace_id: str | None = None,
    ) -> PlanResult:
        """Write a standalone Job + plan file (job mode — skill list, no objectives)."""
        skills = list(skill_names or [])
        ws = safe_workspace_key(workspace_id or f"job-{uuid.uuid4()}")
        project_workspace_dir(ws)

        plan_path = JobPlanner().persist(ws, plan_text, reason="initial")
        job = Job.objects.create(
            title=(title or "adhoc-job").strip() or "adhoc-job",
            description=(description or "").strip(),
            lifecycle=JobLifecycle.SHORT,
            status=JobStatus.PENDING,
            skill_names=skills,
            plan_text=plan_text,
            plan_path=plan_path or "plans/latest.md",
            workspace_id=ws,
        )
        enqueue_job(str(job.id))
        return PlanResult(job=job, plan_text=plan_text, skill_names=skills)


    @classmethod
    def draft_llm_plan(cls, 
        *,
        description: str,
        mode: str = "project",
        title: str = "adhoc",
        summary: str = "",
        in_scope: list | None = None,
        exclusions: list | None = None,
        authorization: str = "",
        skills: list[str] | None = None,
        preferred_tags: list[str] | None = None,
        project: Project | None = None,
    ) -> PlanDraft:
        """Call SkillRouter + planners; return draft (no DB write)."""
        text = (description or "").strip()
        if not text:
            raise ValueError("description is required")

        mode_norm = (mode or "project").strip().lower()
        if mode_norm not in {"project", "job"}:
            raise ValueError("mode must be 'project' or 'job'")

        scope = coerce_targets(in_scope or [])
        excl = coerce_targets(exclusions or [])
        tags = list(preferred_tags or [])
        explicit = list(skills or [])
        resolved = SkillRouter.shared().resolve_default_skills(
            text,
            lifecycle="long" if mode_norm == "project" else "auto",
            explicit=explicit or None,
            project=mode_norm == "project",
            preferred_tags=tags or None,
        )
        prompt = text
        if resolved:
            prompt = f"{text}\n\nPreloaded skills: {', '.join(resolved)}"

        prior_plan = ""
        existing_summary = ""
        if project is not None and mode_norm == "project":
            if not title or title == "adhoc":
                title = project.title or title
            if not summary:
                summary = project.summary or ""
            prior_plan = (
                project.jobs.order_by("-created_at").values_list("plan_text", flat=True).first()
                or ""
            )
            if not prior_plan:
                try:
                    plan_path = (
                        project_workspace_dir(str(project.id), create=False)
                        / "plans"
                        / "latest.md"
                    )
                    if plan_path.is_file():
                        prior_plan = plan_path.read_text(encoding="utf-8", errors="replace")[
                            :4000
                        ]
                except Exception:
                    prior_plan = ""
            lines = []
            for obj in project.objectives.order_by("seq")[:40]:
                lines.append(
                    f"- OBJ-{obj.seq} [{obj.status}] {obj.title} "
                    f"skill={obj.skill_suggestion or '-'}"
                )
            existing_summary = "\n".join(lines)

        llm = chat_model()
        if mode_norm == "project":
            planner = ProjectPlanner()
            msgs = planner.build_messages(
                prompt,
                project_title=title,
                project_summary=summary,
                roe=SimpleNamespace(
                    in_scope=format_targets(scope),
                    exclusions=format_targets(excl),
                    authorization_note=authorization or "",
                    testing_window_notes="",
                    abort_triggers="",
                ),
                filtered_skills_index=SkillRegistry.shared().skills_index(jobable_only=True),
                focus_tags=tags or None,
                prior_plan=prior_plan,
                existing_objectives_summary=existing_summary,
            )
            raw = planner.message_text(llm.invoke(msgs).content)
            payload = parse_project_objectives(raw)
            plan_text = planner.format_result(raw, project_title=title)
            obj_skills = [
                str(o.get("skill_suggestion") or "").strip()
                for o in (payload.get("objectives") or [])
                if str(o.get("skill_suggestion") or "").strip()
            ]
            return PlanDraft(
                mode=mode_norm,
                plan_text=plan_text,
                skill_names=bookend_skill_names([*obj_skills, *resolved]),
                objectives_payload=list(payload.get("objectives") or []),
                description=description,
                title=title,
                summary=summary,
                in_scope=scope,
                exclusions=excl,
                authorization=authorization or "",
                preferred_tags=tags,
            )

        planner = JobPlanner()
        msgs = planner.build_messages(prompt)
        plan_text = planner.format_result(planner.message_text(llm.invoke(msgs).content))
        return PlanDraft(
            mode=mode_norm,
            plan_text=plan_text,
            skill_names=resolved,
            description=description,
            title=title,
            preferred_tags=tags,
        )


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
                skill_names=draft.skill_names,
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
            skill_names=draft.skill_names,
            workspace_id=workspace_id,
        )


    @classmethod
    def run_llm_plan(cls, 
        *,
        description: str,
        mode: str = "project",
        title: str = "adhoc",
        summary: str = "",
        in_scope: list | None = None,
        exclusions: list | None = None,
        authorization: str = "",
        skills: list[str] | None = None,
        preferred_tags: list[str] | None = None,
        project: Project | None = None,
    ) -> PlanResult:
        """Draft via LLM then persist (enqueue first ready objective for projects)."""
        draft = cls.draft_llm_plan(
            description=description,
            mode=mode,
            title=title,
            summary=summary,
            in_scope=in_scope,
            exclusions=exclusions,
            authorization=authorization,
            skills=skills,
            preferred_tags=preferred_tags,
            project=project,
        )
        return cls.persist_draft(draft, project=project)


    @classmethod
    def replan_project(cls, project: Project, *, description: str | None = None) -> PlanResult:
        """Operator replan: revise OPPLAN in place, supersede open objectives, start next ready."""
        roe = getattr(project, "roe", None)
        brief = (description or project.summary or project.title or "").strip()
        if not brief:
            raise ValueError("project needs a summary/description to replan")
        in_scope: list = []
        exclusions: list = []
        auth = ""
        if roe is not None:
            # Re-coerce so duplicate values collapse (type remains a soft hint).
            fixed_scope = coerce_targets(roe.in_scope)
            fixed_excl = coerce_targets(roe.exclusions)
            changed = False
            if fixed_scope != list(roe.in_scope or []):
                roe.in_scope = fixed_scope
                changed = True
            if fixed_excl != list(roe.exclusions or []):
                roe.exclusions = fixed_excl
                changed = True
            if changed:
                roe.save(update_fields=["in_scope", "exclusions", "updated_at"])
            in_scope = list(roe.in_scope or [])
            exclusions = list(roe.exclusions or [])
            auth = (roe.authorization_note or "")
        return cls.run_llm_plan(
            description=brief,
            mode="project",
            title=project.title,
            summary=project.summary,
            in_scope=in_scope,
            exclusions=exclusions,
            authorization=auth,
            preferred_tags=list(project.focus_tags or []),
            project=project,
        )
