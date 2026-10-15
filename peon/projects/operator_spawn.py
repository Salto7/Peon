"""Operator-driven child agent spawn from the war-room agent graph."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone as dj_tz

from orchestrator.crew.roles.registry import (
    RoleRegistry,
)
from orchestrator.skills.bind import resolve_skill_ids
from orchestrator.skills.role_memory import upsert_role_skills
from peon.projects.lifecycle import project_allows_operator, queue_job
from peon.projects.models import (
    Job,
    JobLifecycle,
    JobStatus,
    Objective,
    ObjectiveStatus,
    Project,
)
from peon.projects.objectives import (
    ObjectiveScheduler,
    inherited_objective_dependencies,
)
from peon.projects.tasks import enqueue_job


def _role_skill_payload(role: Any) -> dict[str, Any]:
    return {
        "id": role.id,
        "label": role.label,
        "goal": (role.goal or "")[:180],
        "skills": list(role.skills or ()),
        "kind": getattr(role, "kind", "") or "",
    }


def suggest_roles_for_parent(parent: Job, *, limit: int = 8) -> list[dict[str, Any]]:
    """Roles for add-child: parent role first (inherit), then other specialists."""
    reg = RoleRegistry.shared()
    parent_roles = [str(r).strip() for r in (parent.role_ids or []) if str(r).strip()]
    parent_id = parent_roles[0] if parent_roles else ""
    parent_caps: set[str] = set()
    for rid in parent_roles:
        role = reg.get(rid)
        if role is None:
            continue
        parent_caps.update(c.lower() for c in (role.capabilities or ()))

    mgr_id, ana_id = reg.engagement_bookends()
    out: list[dict[str, Any]] = []
    parent_role = reg.get(parent_id) if parent_id else None
    if (
        parent_role is not None
        and not parent_role.is_manager
        and not parent_role.is_analyzer
    ):
        row = _role_skill_payload(parent_role)
        row["score"] = 100
        row["suggested"] = True
        row["inherit"] = True
        # Prefer skills already on the parent job when present.
        inherited = [
            str(s).strip()
            for s in (getattr(parent, "skill_ids", None) or ())
            if str(s).strip()
        ]
        if inherited:
            row["selected_skills"] = list(
                resolve_skill_ids(parent_role, inherited)
            ) or list(parent_role.skills or ())
        else:
            row["selected_skills"] = list(parent_role.skills or ())
        out.append(row)

    skip = {mgr_id, ana_id, parent_id} - {""}
    scored: list[tuple[int, Any]] = []
    for role in reg.list_roles():
        if role.is_manager or role.is_analyzer:
            continue
        if role.id in skip:
            continue
        caps = {c.lower() for c in (role.capabilities or ())}
        overlap = len(parent_caps & caps)
        score = overlap * 10
        if parent_id and parent_id.split("-")[0] in role.id:
            score += 3
        scored.append((score, role))

    scored.sort(key=lambda pair: (-pair[0], pair[1].label.lower()))
    for score, role in scored[: max(1, limit)]:
        row = _role_skill_payload(role)
        row["score"] = score
        row["suggested"] = score > 0
        row["inherit"] = False
        row["selected_skills"] = list(role.skills or ())
        out.append(row)
    return out


def reopen_analyzer(project: Project) -> Job | None:
    """Re-open the analyzer objective (and its latest job) so the report rewrites."""
    ana = RoleRegistry.shared().analyzer()
    if ana is None:
        return None
    obj = (
        project.objectives.filter(role_id=ana.id)
        .exclude(status=ObjectiveStatus.CANCELLED)
        .order_by("-seq")
        .first()
    )
    if obj is None:
        return None
    if obj.status != ObjectiveStatus.PENDING:
        obj.status = ObjectiveStatus.PENDING
        obj.blocked_reason = ""
        obj.completed_at = None
        obj.started_at = None
        obj.save(
            update_fields=[
                "status",
                "blocked_reason",
                "completed_at",
                "started_at",
                "updated_at",
            ]
        )

    job = (
        project.jobs.filter(objective=obj)
        .order_by("-created_at")
        .first()
    )
    if job is not None:
        queue_job(job)
        return job

    queued = ObjectiveScheduler().enqueue_ready(project)
    for j in queued:
        if ana.id in (j.role_ids or []):
            return j
    return None


def maybe_reopen_analyzer_after_child(job: Job) -> Job | None:
    """When an operator/spawned child finishes, re-run analyzer for fresh artifacts."""
    if not job.project_id or not job.parent_id:
        return None
    roles = [str(r).strip() for r in (job.role_ids or []) if str(r).strip()]
    registry = RoleRegistry.shared()
    ana = registry.analyzer()
    if ana and ana.id in roles:
        return None
    mgr_id, _ = registry.engagement_bookends()
    if mgr_id and mgr_id in roles:
        return None
    return reopen_analyzer(job.project)


def add_child_agent(
    project: Project,
    parent: Job,
    *,
    role_id: str = "",
    title: str,
    prompt: str,
    skill_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Insert a specialist objective + child Job under ``parent``, keep analyzer last."""
    from peon.projects.db import ensure_project_database, using_project

    project_allows_operator(project)
    if parent.project_id != project.id:
        raise ValueError("parent job is not on this project")

    reg = RoleRegistry.shared()
    rid = str(role_id or "").strip()
    if not rid:
        parent_roles = [str(r).strip() for r in (parent.role_ids or []) if str(r).strip()]
        rid = parent_roles[0] if parent_roles else ""
    role = reg.get(rid)
    if role is None:
        raise ValueError(f"unknown role_id {rid!r}")
    if role.is_manager or role.is_analyzer:
        raise ValueError(f"cannot spawn {role.id} as a child specialist")

    selected = list(resolve_skill_ids(role, skill_ids))
    if not selected and role.skills:
        selected = list(role.skills)

    title_s = (title or "").strip() or f"{role.label} follow-up"
    prompt_s = (prompt or "").strip()
    if not prompt_s:
        raise ValueError("task prompt is required")

    alias = ensure_project_database(project.id)
    with using_project(project.id):
        with transaction.atomic(using=alias):
            result = _add_child_agent_locked(
                project,
                parent,
                alias=alias,
                role=role,
                title_s=title_s,
                prompt_s=prompt_s,
                selected=selected,
                reg=reg,
            )
    enqueue_job(str(result["job_id"]))
    upsert_role_skills(
        role.id,
        selected,
        project_id=str(project.id),
        source="operator_spawn",
    )
    return result


def _add_child_agent_locked(
    project: Project,
    parent: Job,
    *,
    alias: str,
    role,
    title_s: str,
    prompt_s: str,
    selected: list[str],
    reg,
) -> dict[str, Any]:
    ana = reg.analyzer()
    objs = list(
        project.objectives.exclude(status=ObjectiveStatus.CANCELLED).order_by("seq")
    )
    analyzer_obj = next(
        (o for o in objs if ana and o.role_id == ana.id),
        objs[-1] if objs else None,
    )

    parent_obj = parent.objective
    parent_seq = int(parent_obj.seq) if parent_obj is not None else 1

    if analyzer_obj is not None:
        insert_seq = int(analyzer_obj.seq)
        for o in objs:
            if o.seq >= insert_seq:
                o.seq = o.seq + 1
                o.save(using=alias, update_fields=["seq", "updated_at"])
        new_seq = insert_seq
    else:
        new_seq = max((o.seq for o in objs), default=0) + 1

    child_obj = Objective.objects.using(alias).create(
        project=project,
        seq=new_seq,
        title=title_s[:255],
        description=prompt_s,
        phase=(parent_obj.phase if parent_obj is not None else "recon"),
        acceptance_criteria="Observable completion of the operator-requested task",
        status=ObjectiveStatus.PENDING,
        role_id=role.id,
        skill_ids=selected,
        commands=[],
    )
    # Preserve all upstream gates and add the parent objective itself. Any
    # child-specific gates can be unioned through the shared helper as this
    # API grows without weakening inherited ordering.
    child_obj.depends_on.set(inherited_objective_dependencies(parent_obj))

    if analyzer_obj is not None:
        analyzer_obj.refresh_from_db()
        current = list(analyzer_obj.depends_on.all())
        if child_obj not in current:
            analyzer_obj.depends_on.add(child_obj)
        if analyzer_obj.status in {
            ObjectiveStatus.COMPLETED,
            ObjectiveStatus.BLOCKED,
            ObjectiveStatus.CANCELLED,
        }:
            analyzer_obj.status = ObjectiveStatus.PENDING
            analyzer_obj.blocked_reason = ""
            analyzer_obj.completed_at = None
            analyzer_obj.started_at = None
            analyzer_obj.save(
                using=alias,
                update_fields=[
                    "status",
                    "blocked_reason",
                    "completed_at",
                    "started_at",
                    "updated_at",
                ],
            )

    child = Job.objects.using(alias).create(
        title=title_s[:255],
        description=prompt_s,
        lifecycle=JobLifecycle.LONG,
        status=JobStatus.PENDING,
        role_ids=[role.id],
        skill_ids=selected,
        project=project,
        objective=child_obj,
        parent=parent,
        workspace_id=parent.workspace_id,
        plan_text=parent.plan_text or "",
        plan_path=parent.plan_path or "",
    )

    return {
        "ok": True,
        "job_id": str(child.id),
        "objective_id": str(child_obj.id),
        "objective_seq": child_obj.seq,
        "parent_job_id": str(parent.id),
        "parent_objective_seq": parent_seq,
        "role_id": role.id,
        "skill_ids": selected,
        "title": child.title,
        "created_at": dj_tz.now().isoformat(),
    }
