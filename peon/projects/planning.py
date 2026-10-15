"""Project/job planning: drafts, validation, and DB persistence."""

from __future__ import annotations

import logging

import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from django.db import transaction

from orchestrator.crew.roles.registry import RoleRegistry
from orchestrator.crew.router import RoleRouter
from orchestrator.skills.role_memory import role_skills_prompt_block
from orchestrator.planning import (
    JobPlanner,
    ProjectPlanner,
    bookend_project_objectives,
    bookend_role_ids,
    parse_project_objectives,
)
from orchestrator.utils.llm import chat_messages
from peon.projects.models import (
    Job,
    JobLifecycle,
    JobStatus,
    Project,
    ProjectKind,
    ProjectStatus,
    RulesOfEngagement,
    SandboxRuntime,
)
from peon.projects.objectives import ObjectivePlanSync, ObjectiveScheduler
from peon.projects.roe_ops import provision_project_roe
from peon.projects.target_shapes import (
    coerce_targets,
    extract_targets,
    format_targets,
)
from peon.projects.tasks import enqueue_job
from peon.projects.workspaces import platform_workspace_dir, project_workspace_dir, safe_workspace_key

logger = logging.getLogger(__name__)


@dataclass
class PlanDraft:
    """LLM plan output before DB persist."""

    mode: str
    plan_text: str
    role_ids: list[str] = field(default_factory=list)
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
    role_ids: list[str] | None = None


def validate_project_objectives(objectives_payload: list[dict[str, Any]]) -> None:
    """Reject plans that cannot create a runnable CrewAI objective graph."""

    registry = RoleRegistry.shared()
    roles = registry.list_roles()
    if not roles:
        raise ValueError(
            "No CrewAI roles found — check orchestrator/crew/roles/catalog.py"
        )
    manager = registry.manager()
    analyzer = registry.analyzer()
    if manager is None or analyzer is None:
        missing = []
        if manager is None:
            missing.append("engagement manager (allow_delegation, empty reports_to)")
        if analyzer is None:
            missing.append("reporting bookend (capabilities include report)")
        raise ValueError("CrewAI role catalog is missing: " + ", ".join(missing))

    rows = [row for row in objectives_payload if isinstance(row, dict)]
    if not rows:
        raise ValueError("Planner returned no objectives")

    from orchestrator.crew.roles.catalog import resolve_role_id

    known = {role.id for role in roles}
    invalid: list[str] = []
    for index, row in enumerate(rows, start=1):
        if not str(row.get("title") or "").strip():
            raise ValueError(f"Planner objective {index} has no title")
        role_id = resolve_role_id(str(row.get("role_id") or "").strip())
        if not role_id:
            raise ValueError(f"Planner objective {index} has no role_id")
        if role_id not in known and role_id not in invalid:
            invalid.append(role_id)
    if invalid:
        raise ValueError(
            "Planner used unknown CrewAI role id(s): " + ", ".join(invalid)
        )
    if str(rows[0].get("role_id") or "").strip() != manager.id:
        raise ValueError(f"First objective must use manager role {manager.id}")
    if str(rows[-1].get("role_id") or "").strip() != analyzer.id:
        raise ValueError(f"Last objective must use analyzer role {analyzer.id}")


def draft_llm_plan(*,
    description: str,
    mode: str = "project",
    title: str = "adhoc",
    summary: str = "",
    in_scope: list | None = None,
    exclusions: list | None = None,
    authorization: str = "",
    role_ids: list[str] | None = None,
    preferred_tags: list[str] | None = None,
    project: Project | None = None,
) -> PlanDraft:
    """Call RoleRouter + planners; return draft (no DB write)."""
    text = (description or "").strip()
    if not text:
        raise ValueError("description is required")

    mode_norm = (mode or "project").strip().lower()
    if mode_norm not in {"project", "job"}:
        raise ValueError("mode must be 'project' or 'job'")

    scope = coerce_targets(in_scope or [])
    excl = coerce_targets(exclusions or [])
    tags = list(preferred_tags or [])
    explicit = list(role_ids or [])
    resolved = RoleRouter.shared().resolve(
        text,
        explicit=explicit or None,
        project=mode_norm == "project",
    )
    prompt = text
    if resolved:
        prompt = f"{text}\n\nPreloaded roles: {', '.join(resolved)}"

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
                f"role={obj.role_id or '-'}"
            )
        existing_summary = "\n".join(lines)

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
            filtered_roles_index=(
                RoleRouter.shared().roles_index_text()
                + (
                    ("\n\n" + _rs)
                    if (_rs := role_skills_prompt_block())
                    else ""
                )
            ),
            focus_tags=tags or None,
            prior_plan=prior_plan,
            existing_objectives_summary=existing_summary,
        )
        raw = planner.message_text(chat_messages(msgs))
        payload = parse_project_objectives(raw)
        plan_text = planner.format_result(raw, project_title=title)
        obj_roles = [
            str(o.get("role_id") or "").strip()
            for o in (payload.get("objectives") or [])
            if str(o.get("role_id") or "").strip()
        ]
        return PlanDraft(
            mode=mode_norm,
            plan_text=plan_text,
            role_ids=bookend_role_ids([*obj_roles, *resolved]),
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
    plan_text = planner.format_result(planner.message_text(chat_messages(msgs)))
    return PlanDraft(
        mode=mode_norm,
        plan_text=plan_text,
        role_ids=resolved,
        description=description,
        title=title,
        preferred_tags=tags,
    )


def run_llm_plan(*,
    description: str,
    mode: str = "project",
    title: str = "adhoc",
    summary: str = "",
    in_scope: list | None = None,
    exclusions: list | None = None,
    authorization: str = "",
    role_ids: list[str] | None = None,
    preferred_tags: list[str] | None = None,
    project: Project | None = None,
) -> PlanResult:
    """Draft via LLM then persist (enqueue first ready objective for projects)."""
    draft = draft_llm_plan(
        description=description,
        mode=mode,
        title=title,
        summary=summary,
        in_scope=in_scope,
        exclusions=exclusions,
        authorization=authorization,
        role_ids=role_ids,
        preferred_tags=preferred_tags,
        project=project,
    )
    start = True
    if project is not None and project.kind == ProjectKind.DURABLE:
        start = False
    return persist_draft(draft, project=project, start=start)


def replan_project(project: Project, *, description: str | None = None) -> PlanResult:
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
    return run_llm_plan(
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


@transaction.atomic
def persist_project_plan(*,
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
    del role_ids, workspace_id  # project identity owns its workspace key
    scope = coerce_targets(in_scope or [])
    excl = coerce_targets(exclusions or [])
    tags = list(preferred_tags or [])
    auth = (authorization or "").strip()

    objectives_payload = bookend_project_objectives(list(objectives_payload or []))
    validate_project_objectives(objectives_payload)

    from peon.projects.db import ensure_project_database, using_project

    if project is None:
        project = Project.objects.create(
            title=(title or "adhoc").strip() or "adhoc",
            summary=(summary or "").strip(),
            status=ProjectStatus.ACTIVE,
            focus_tags=tags,
        )
        ensure_project_database(project.id)
        project_workspace_dir(str(project.id))
        if not scope:
            scope = extract_targets(description, title, summary)
        with using_project(project.id):
            RulesOfEngagement.objects.using(
                ensure_project_database(project.id)
            ).create(
                project=project,
                in_scope=scope,
                exclusions=excl,
                seed=(
                    [
                        {
                            "type": "other",
                            "value": (summary or description or title)[:500],
                        }
                    ]
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
        ensure_project_database(project.id)
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
        alias = ensure_project_database(project.id)
        if scope:
            from peon.projects.roe_ops import get_or_create_roe

            with using_project(project.id):
                roe = get_or_create_roe(project)
                roe.in_scope = scope
                if excl:
                    roe.exclusions = excl
                if auth:
                    roe.authorization_note = auth
                roe.save(using=alias)
        else:
            provision_project_roe(
                project,
                texts=[description, title, summary],
                exclusions=excl or None,
                authorization=auth,
            )

    ws = safe_workspace_key(str(project.id))
    project_workspace_dir(ws)
    ensure_project_database(project.id)
    created = ObjectivePlanSync().sync(
        project, objectives_payload, replace=replace_objectives
    )
    ProjectPlanner().persist(
        ws, plan_text, reason="replan" if replace_objectives else "initial"
    )

    job = None
    if start:
        sched = ObjectiveScheduler()
        queued = sched.enqueue_ready(project)
        job = queued[0] if queued else None

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


@transaction.atomic
def persist_job_plan(*,
    description: str,
    plan_text: str,
    title: str = "adhoc-job",
    role_ids: list[str] | None = None,
    workspace_id: str | None = None,
) -> PlanResult:
    """Write a standalone Job + plan file (job mode — role list, no objectives)."""
    roles = list(role_ids or [])
    ws = safe_workspace_key(workspace_id or f"job-{uuid.uuid4()}")
    plan_path = JobPlanner().persist(
        ws,
        plan_text,
        reason="initial",
        root=platform_workspace_dir() / "jobs",
    )
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


def persist_draft(draft: PlanDraft,
    *,
    workspace_id: str | None = None,
    project: Project | None = None,
    start: bool | None = None,
) -> PlanResult:
    """Persist a PlanDraft to DB + disk."""
    if draft.mode == "project":
        auto_start = True if start is None else bool(start)
        if project is not None and project.kind == ProjectKind.DURABLE:
            # Durable plans require operator acceptance of the agent graph.
            auto_start = False if start is None else bool(start)
        return persist_project_plan(
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
            start=auto_start,
        )
    return persist_job_plan(
        description=draft.description,
        plan_text=draft.plan_text,
        title=draft.title,
        role_ids=draft.role_ids,
        workspace_id=workspace_id,
    )


def create_project_shell(
    *,
    title: str,
    summary: str = "",
    in_scope: list | None = None,
    path_values: list[str] | None = None,
    operator_supplied_scope: bool = False,
    sandbox_runtime: str = "",
    kind: str = "",
) -> tuple[Project, list, RulesOfEngagement | None]:
    """Create Project + RoE shell (before optional LLM plan).

    Returns ``(project, scope, roe)``. ``path_values`` are sandbox input
    paths folded into seed + in_scope.
    """
    title = (title or "").strip() or "untitled"
    summary = (summary or "").strip()
    scope = coerce_targets(in_scope or [])
    paths = [str(p).strip() for p in (path_values or []) if str(p).strip()]
    kind_norm = ProjectKind.resolve(kind)
    from peon.projects.db import ensure_project_database, using_project

    project = Project.objects.create(
        title=title,
        summary=summary,
        status=ProjectStatus.ACTIVE,
        sandbox_runtime=SandboxRuntime.resolve(sandbox_runtime),
        kind=kind_norm,
        # Durable waits for plan-graph acceptance before enqueue.
        plan_accepted=kind_norm != ProjectKind.DURABLE,
    )
    alias = ensure_project_database(project.id)
    project_workspace_dir(str(project.id))
    if not scope:
        # Prefer the operator brief over the project title (titles are labels,
        # not subjects — e.g. ``qwqkodqwdk`` must not become in-scope).
        scope = extract_targets(summary) if summary else []
        if not scope and title:
            # Title alone only when it looks like a real asset name (Brand).
            scope = extract_targets(title)
    if not scope and (summary or title).strip():
        # Bare asset names / free-text subjects in the brief.
        from peon.projects.target_discovery import SubjectCanonicalizer

        brief = "\n".join(p for p in (summary, title) if (p or "").strip())
        scope = coerce_targets(SubjectCanonicalizer.suggest_assets(brief))

    seed: list = []
    if summary and not scope:
        seed.append({"type": "other", "value": summary[:500]})
    elif summary and scope:
        # Keep operator intent visible even when subjects were deduced.
        seed.append({"type": "other", "value": summary[:500]})
    for sp in paths:
        seed.append({"type": "path", "value": sp})
        scope = list(scope) + [{"type": "path", "value": sp}]

    with using_project(project.id):
        RulesOfEngagement.objects.using(alias).create(
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
    try:
        roe = project.roe
    except RulesOfEngagement.DoesNotExist:
        roe = None
    final_scope = list(roe.in_scope) if roe else coerce_targets(scope)
    try:
        from orchestrator.crew.memory import MemoryService

        scope_hints: list[str] = []
        for item in final_scope or []:
            if isinstance(item, dict):
                val = str(item.get("value") or "").strip()
                if val:
                    scope_hints.append(val)
        # Write workspace file only here — do not mutate summary before the
        # LLM plan runs (bootstrap text was polluting objectives / validation).
        recall = MemoryService.recall_for_new_project(
            title=title,
            summary=summary,
            scope_hints=scope_hints[:24],
        )
        if recall:
            MemoryService.write_project_bootstrap(str(project.id), recall)
    except Exception as exc:
        logger.warning("new-project memory recall failed: %s", exc, exc_info=True)
    return project, final_scope, roe
