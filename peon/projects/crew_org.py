"""Org-chart payload for CrewAI roles (hierarchy from reports_to + project jobs)."""

from __future__ import annotations

from typing import Any

from peon.projects.models import Job, Project


def org_chart_for_project(project: Project) -> dict[str, Any]:
    """Return manager → reports tree for roles used on this project.

    Hierarchy edges come from ROLE.yaml ``reports_to``. Job status overlays
    live project context when a job carries that role_id.
    """
    from orchestrator.crew.roles.registry import RoleRegistry

    reg = RoleRegistry.shared()
    jobs = list(project.jobs.select_related("objective").order_by("-created_at")[:120])

    used_ids: list[str] = []
    seen: set[str] = set()
    for job in jobs:
        for raw in job.role_ids or []:
            rid = str(raw).strip()
            if rid and rid not in seen and reg.get(rid):
                seen.add(rid)
                used_ids.append(rid)
        obj = getattr(job, "objective", None)
        if obj is not None:
            rid = str(obj.role_id or "").strip()
            if rid and rid not in seen and reg.get(rid):
                seen.add(rid)
                used_ids.append(rid)

    # Include supervisors so the tree can root even if manager job not yet spawned.
    for rid in list(used_ids):
        role = reg.get(rid)
        while role and role.reports_to and role.reports_to not in seen:
            parent = reg.get(role.reports_to)
            if parent is None:
                break
            seen.add(parent.id)
            used_ids.append(parent.id)
            role = parent

    if not used_ids:
        # Empty project: show full engagement hierarchy as a preview.
        used_ids = [
            r.id
            for r in reg.list_roles()
            if not r.is_authoring
        ]

    def job_for_role(role_id: str) -> Job | None:
        for job in jobs:
            names = job.role_ids or []
            if role_id in names:
                return job
        return None

    by_id: dict[str, dict[str, Any]] = {}
    for rid in used_ids:
        role = reg.get(rid)
        if role is None:
            continue
        job = job_for_role(role.id)
        by_id[role.id] = {
            "id": role.id,
            "label": role.label,
            "crew_role": role.crew_role,
            "reports_to": role.reports_to or "",
            "tools": list(role.tools),
            "capabilities": list(role.capabilities),
            "requires_roe": bool(role.requires_roe),
            "assets": list(role.assets),
            "mode": role.mode,
            "job_id": str(job.id) if job else "",
            "job_status": job.status if job else "",
            "job_title": job.title if job else "",
            "children": [],
        }

    roots: list[dict[str, Any]] = []
    for node in by_id.values():
        parent_id = node["reports_to"]
        if parent_id and parent_id in by_id and parent_id != node["id"]:
            by_id[parent_id]["children"].append(node)
        else:
            roots.append(node)

    # Stable order: managers first, then label.
    roots.sort(key=lambda n: (0 if not n["reports_to"] else 1, n["label"]))
    for node in by_id.values():
        node["children"].sort(key=lambda n: n["label"])

    return {
        "crew_status": getattr(project, "crew_status", "") or "",
        "crew_flow_id": getattr(project, "crew_flow_id", "") or "",
        "roots": roots,
        "roles": list(by_id.values()),
    }
