"""Shared role/job hierarchy for ops graph and org-chart adapters."""

from __future__ import annotations

from typing import Any

from orchestrator.crew.roles.registry import RoleRegistry
from peon.projects.models import Job, Project


class RoleJobTree:
    """ROLE.yaml ``reports_to`` + Job.parent hierarchy helpers."""

    @staticmethod
    def primary_role_id(job: Job) -> str:
        for raw in job.role_ids or []:
            rid = str(raw).strip()
            if rid:
                return rid
        obj = getattr(job, "objective", None)
        if obj is not None:
            return str(obj.role_id or "").strip()
        return ""

    @staticmethod
    def reports_to(role_id: str) -> str:
        if not role_id:
            return ""
        try:
            role = RoleRegistry.shared().get(role_id)
            return (role.reports_to or "") if role else ""
        except Exception:
            return ""

    @staticmethod
    def role_meta(role_id: str) -> dict[str, Any]:
        if not role_id:
            return {
                "role_label": "",
                "crew_role": "",
                "role_goal": "",
                "capabilities": [],
            }
        try:
            role = RoleRegistry.shared().get(role_id)
        except Exception:
            role = None
        if role is None:
            return {
                "role_label": role_id,
                "crew_role": "",
                "role_goal": "",
                "capabilities": [],
            }
        return {
            "role_label": role.label or role_id,
            "crew_role": role.crew_role or "",
            "role_goal": (role.goal or "").strip()[:240],
            "capabilities": list(role.capabilities or [])[:12],
        }

    @classmethod
    def jobs_by_role(cls, jobs: list[Job]) -> dict[str, Job]:
        """Latest job per primary role id (first seen wins when newest-first)."""
        by_role: dict[str, Job] = {}
        for job in jobs:
            rid = cls.primary_role_id(job)
            if rid and rid not in by_role:
                by_role[rid] = job
        return by_role

    @classmethod
    def _root_sort_key(cls, job: Job) -> tuple:
        obj = getattr(job, "objective", None)
        seq = obj.seq if obj is not None else 10**9
        created = job.created_at.timestamp() if job.created_at else 0
        reports = 0 if not cls.reports_to(cls.primary_role_id(job)) else 1
        return (reports, seq, created)

    @classmethod
    def build_job_hierarchy(cls, jobs: list[Job]) -> tuple[list[Job], dict[str, list[Job]]]:
        """Return (roots, children_by_parent_id) using reports_to then Job.parent."""
        by_id = {str(j.id): j for j in jobs}
        by_role = cls.jobs_by_role(jobs)
        children: dict[str, list[Job]] = {str(j.id): [] for j in jobs}
        roots: list[Job] = []
        attached: set[str] = set()

        for job in jobs:
            jid = str(job.id)
            primary = cls.primary_role_id(job)
            supervisor_role = cls.reports_to(primary)
            supervisor_job = by_role.get(supervisor_role) if supervisor_role else None
            if (
                supervisor_job is not None
                and str(supervisor_job.id) != jid
                and str(supervisor_job.id) in children
            ):
                children[str(supervisor_job.id)].append(job)
                attached.add(jid)
                continue
            if job.parent_id and str(job.parent_id) in by_id:
                children[str(job.parent_id)].append(job)
                attached.add(jid)
                continue
            roots.append(job)

        roots = [j for j in roots if str(j.id) not in attached]
        roots = sorted(roots, key=cls._root_sort_key)
        for kids in children.values():
            kids.sort(key=lambda j: j.created_at.timestamp() if j.created_at else 0)
        return roots, children

    @classmethod
    def used_role_ids(cls, jobs: list[Job]) -> list[str]:

        reg = RoleRegistry.shared()
        used: list[str] = []
        seen: set[str] = set()
        for job in jobs:
            for raw in job.role_ids or []:
                rid = str(raw).strip()
                if rid and rid not in seen and reg.get(rid):
                    seen.add(rid)
                    used.append(rid)
            obj = getattr(job, "objective", None)
            if obj is not None:
                rid = str(obj.role_id or "").strip()
                if rid and rid not in seen and reg.get(rid):
                    seen.add(rid)
                    used.append(rid)

        for rid in list(used):
            role = reg.get(rid)
            while role and role.reports_to and role.reports_to not in seen:
                parent = reg.get(role.reports_to)
                if parent is None:
                    break
                seen.add(parent.id)
                used.append(parent.id)
                role = parent

        if not used:
            used = [r.id for r in reg.list_roles() if not r.is_authoring]
        return used

    @classmethod
    def org_nodes_for_project(cls, project: Project) -> dict[str, Any]:
        """Role-centric org chart (thin adapter over shared helpers)."""

        reg = RoleRegistry.shared()
        jobs = list(project.jobs.select_related("objective").order_by("-created_at")[:120])
        by_role = cls.jobs_by_role(jobs)
        used_ids = cls.used_role_ids(jobs)

        by_id: dict[str, dict[str, Any]] = {}
        for rid in used_ids:
            role = reg.get(rid)
            if role is None:
                continue
            job = by_role.get(role.id)
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

        roots.sort(key=lambda n: (0 if not n["reports_to"] else 1, n["label"]))
        for node in by_id.values():
            node["children"].sort(key=lambda n: n["label"])

        return {
            "crew_status": getattr(project, "crew_status", "") or "",
            "crew_flow_id": getattr(project, "crew_flow_id", "") or "",
            "roots": roots,
            "roles": list(by_id.values()),
        }
