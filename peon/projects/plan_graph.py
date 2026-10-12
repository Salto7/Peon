"""Durable plan as an editable agent graph (objectives ↔ agents)."""

from __future__ import annotations

import json
from typing import Any

from orchestrator.crew.roles.registry import RoleRegistry
from orchestrator.planning import bookend_project_objectives
from peon.projects.models import Objective, ObjectiveStatus, Project
from peon.projects.objectives import ObjectiveScheduler
from peon.projects.planning import validate_project_objectives


def plan_agent_nodes(project: Project) -> list[dict[str, Any]]:
    """Serialize objectives as editable agent-plan nodes."""
    nodes: list[dict[str, Any]] = []
    for obj in project.objectives.exclude(status=ObjectiveStatus.CANCELLED).order_by(
        "seq"
    ):
        dep_seqs = [d.seq for d in obj.depends_on.all()]
        nodes.append(
            {
                "id": str(obj.id),
                "seq": obj.seq,
                "title": obj.title,
                "description": obj.description or "",
                "acceptance_criteria": obj.acceptance_criteria or "",
                "phase": obj.phase,
                "role_id": obj.role_id or "",
                "skill_ids": list(obj.skill_ids or []),
                "commands": list(obj.commands or []),
                "status": obj.status,
                "depends_on": dep_seqs,
                "prompt": (obj.description or "").strip(),
            }
        )
    return nodes


def plan_graph_payload(project: Project) -> dict[str, Any]:
    from peon.projects.schedules import schedules_payload

    roles = [
        {
            "id": r.id,
            "label": r.label,
            "goal": (r.goal or "")[:160],
            "skills": list(r.skills or ()),
            "kind": r.kind,
        }
        for r in RoleRegistry.shared().list_roles()
    ]
    return {
        "project_id": str(project.id),
        "kind": project.kind,
        "plan_accepted": bool(project.plan_accepted),
        "title": project.title,
        "agents": plan_agent_nodes(project),
        "schedules": schedules_payload(project),
        "roles": roles,
    }


def _payload_to_objectives(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, raw in enumerate(agents or [], start=1):
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        role_id = str(raw.get("role_id") or "").strip()
        if not title or not role_id:
            continue
        prompt = str(raw.get("prompt") or raw.get("description") or "").strip()
        cmds = raw.get("commands") or []
        if isinstance(cmds, str):
            try:
                cmds = json.loads(cmds)
            except json.JSONDecodeError:
                cmds = [c.strip() for c in cmds.splitlines() if c.strip()]
        if not isinstance(cmds, list):
            cmds = []
        deps = raw.get("depends_on") or []
        if not isinstance(deps, list):
            deps = []
        dep_idxs: list[int] = []
        for d in deps:
            try:
                dep_idxs.append(int(d))
            except (TypeError, ValueError):
                continue
        skills_raw = raw.get("skill_ids") or []
        if not isinstance(skills_raw, list):
            skills_raw = []
        role = RoleRegistry.shared().get(role_id)
        if role is not None:
            from orchestrator.skills.bind import resolve_skill_ids

            skill_ids = list(resolve_skill_ids(role, skills_raw)) or list(
                role.skills or ()
            )
        else:
            skill_ids = [str(s).strip() for s in skills_raw if str(s).strip()]
        rows.append(
            {
                "seq": int(raw.get("seq") or i),
                "title": title,
                "description": prompt,
                "acceptance_criteria": str(raw.get("acceptance_criteria") or "").strip(),
                "phase": str(raw.get("phase") or "recon").strip() or "recon",
                "role_id": role_id,
                "skill_ids": skill_ids,
                "commands": [str(c).strip() for c in cmds if str(c).strip()][:8],
                "depends_on": dep_idxs,
                "mitre": [],
            }
        )
    return bookend_project_objectives(rows)


def save_plan_agents(project: Project, agents: list[dict[str, Any]]) -> list[Objective]:
    """Replace project objectives from an edited agent graph."""
    from orchestrator.skills.role_memory import upsert_role_skills
    from peon.projects.objectives import ObjectivePlanSync

    # ObjectivePlanSync owns the project-DB transaction; do not wrap default.
    payload = _payload_to_objectives(agents)
    validate_project_objectives(payload)
    objs = ObjectivePlanSync().sync(project, payload, replace=True)
    for row in payload:
        rid = str(row.get("role_id") or "").strip()
        if rid:
            upsert_role_skills(
                rid,
                row.get("skill_ids") or [],
                project_id=str(project.id),
                source="plan_editor",
            )
    return objs


def accept_plan(project: Project) -> dict[str, Any]:
    """Mark plan accepted and enqueue ready jobs (durable start)."""
    if not project.plan_accepted:
        project.plan_accepted = True
        project.save(update_fields=["plan_accepted", "updated_at"])
    queued = ObjectiveScheduler().enqueue_ready(project)
    return {
        "ok": True,
        "plan_accepted": True,
        "kind": project.kind,
        "queued": [str(j.id) for j in queued],
        "count": len(queued),
    }
