"""War-room ops console JSON (agents, roles, progress)."""


from __future__ import annotations

from django.db.models import Count

from orchestrator.utils.commands import (
    capability_names_payload,
    cli_names_payload,
    pick_agent_command,
)
from orchestrator.utils.stream_events import display_command
from peon.projects.catalog_cards.role import RoleCards
from peon.projects.console import pending_operator_prompts
from peon.projects.models import (
    TERMINAL_JOB_STATUSES,
    Job,
    Objective,
    ObjectiveStatus,
    Project,
    StreamMessage,
    StreamMessageType,
)
from peon.projects.objectives import ObjectiveScheduler
from peon.projects.role_job_tree import RoleJobTree
from peon.projects.sandbox import ProjectSandbox
from peon.projects.workspaces import reports_payload

# Prefer tool lines; fall back to recent log/status for graph snippets.
_COMMAND_TYPES = (
    StreamMessageType.TOOL,
    StreamMessageType.LOG,
    StreamMessageType.STATUS,
    StreamMessageType.RESULT,
)


class ProjectOpsPayload:
    """Build JSON for the war-room ops console (agents, roles, progress)."""

    @classmethod
    def job_payload(cls, job: Job) -> dict:
        return {
            "id": str(job.id),
            "title": job.title,
            "status": job.status,
            "profile": job.profile or "",
            "role_ids": job.role_ids or [],
            "parent_id": str(job.parent_id) if job.parent_id else "",
            "error": job.error,
            "updated_at": job.updated_at.isoformat() if job.updated_at else "",
            "terminal": job.status in TERMINAL_JOB_STATUSES,
        }

    @classmethod
    def _objective_fields(cls, job: Job) -> dict:
        obj = getattr(job, "objective", None)
        if obj is None:
            return {
                "objective_id": "",
                "objective_seq": None,
                "objective_title": "",
                "objective_description": "",
                "objective_acceptance": "",
                "objective_phase": "",
            }
        return {
            "objective_id": str(obj.id),
            "objective_seq": obj.seq,
            "objective_title": obj.title or "",
            "objective_description": (obj.description or "").strip(),
            "objective_acceptance": (obj.acceptance_criteria or "").strip(),
            "objective_phase": obj.phase or "",
        }

    @classmethod
    def _agent_base(cls, job: Job, *, role: str) -> dict:

        obj = getattr(job, "objective", None)
        obj_cmds = [
            str(c).strip() for c in ((obj.commands if obj is not None else None) or [])
            if str(c).strip()
        ]
        description = (job.description or "").strip()
        # Edit/re-run targets agent-emitted CLI/tool lines — never the full brief.
        operator_command = pick_agent_command(*obj_cmds)
        primary = RoleJobTree.primary_role_id(job)
        meta = RoleJobTree.role_meta(primary)
        return {
            "id": str(job.id),
            "title": job.title,
            "status": job.status,
            "profile": job.profile or "",
            "role_ids": job.role_ids or [],
            "primary_role_id": primary,
            "role_label": meta["role_label"],
            "crew_role": meta["crew_role"],
            "role_goal": meta["role_goal"],
            "capabilities": meta["capabilities"],
            "reports_to": RoleJobTree.reports_to(primary),
            "terminal": job.status in TERMINAL_JOB_STATUSES,
            "updated_at": job.updated_at.isoformat() if job.updated_at else "",
            "error": job.error or "",
            "role": role,
            "description": description,
            "operator_command": operator_command,
            "last_command": "",
            "commands": list(obj_cmds),
            "tool_calls": 0,
            **cls._objective_fields(job),
        }

    @classmethod
    def agent_payload(cls, 
        job: Job, *, subagents: list[Job] | None = None, role: str = "ROOT"
    ) -> dict:
        kids = subagents if subagents is not None else list(job.children.all())
        payload = cls._agent_base(job, role=role)
        payload["subagents"] = [
            {**cls._agent_base(c, role="SUB"), "subagents": []} for c in kids
        ]
        return payload

    @classmethod
    def objective_payload(cls, obj: Objective) -> dict:
        return {
            "id": str(obj.id),
            "seq": obj.seq,
            "title": obj.title,
            "phase": obj.phase,
            "description": (obj.description or "").strip(),
            "acceptance_criteria": (obj.acceptance_criteria or "").strip(),
            "role_id": obj.role_id or "",
            "status": obj.status,
        }

    @classmethod
    def sandbox_payload(cls, project: Project) -> dict:
        runtime_id = getattr(project, "sandbox_runtime", "") or "sandbox"
        if runtime_id == "openshell":
            mode = "openshell"
        else:
            mode = "docker" if ProjectSandbox.shared().per_project() else "shared"
        return {
            "name": ProjectSandbox.container_name(str(project.id)),
            "mode": mode,
        }

    @classmethod
    def progress_payload(cls, *, objectives: list[Objective], jobs: list[Job]) -> dict:
        obj_total = len(objectives)
        obj_done = sum(1 for o in objectives if o.status == ObjectiveStatus.COMPLETED)
        job_total = len(jobs)
        job_done = sum(1 for j in jobs if j.status in TERMINAL_JOB_STATUSES)
        if obj_total:
            ratio = obj_done / obj_total
        elif job_total:
            ratio = job_done / job_total
        else:
            ratio = 0.0
        return {
            "objectives_done": obj_done,
            "objectives_total": obj_total,
            "jobs_done": job_done,
            "jobs_total": job_total,
            "ratio": round(ratio, 4),
        }

    @classmethod
    def active_phase(cls, objectives: list[Objective]) -> str:
        for obj in objectives:
            if obj.status == ObjectiveStatus.IN_PROGRESS:
                return obj.phase or ""
        for obj in objectives:
            if obj.status == ObjectiveStatus.PENDING:
                return obj.phase or ""
        return ""

    @classmethod
    def roles_used_from_jobs(cls, jobs: list[Job]) -> list[dict]:
        names: list[str] = []
        seen: set[str] = set()
        for job in jobs:
            for raw in job.role_ids or []:
                name = str(raw).strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                names.append(name)
        return RoleCards.for_names(names)

    @classmethod
    def agents_tree(cls, jobs: list[Job]) -> list[dict]:
        """Build agent graph: ROLE.yaml reports_to first, then Job.parent edges."""

        roots, children = RoleJobTree.build_job_hierarchy(jobs)

        def _payload(job: Job, *, role: str) -> dict:
            kids = children.get(str(job.id), [])
            payload = cls._agent_base(job, role=role)
            payload["subagents"] = [_payload(c, role="SUB") for c in kids]
            return payload

        return [_payload(root, role="ROOT") for root in roots]

    @classmethod
    def recent_activity_by_job(cls, 
        job_ids: list[str], *, per_job: int = 4
    ) -> dict[str, dict]:
        """Latest command snippets + tool-call counts keyed by job id."""

        ids = [str(j) for j in job_ids if j]
        empty: dict[str, dict] = {
            jid: {"commands": [], "last_command": "", "tool_calls": 0} for jid in ids
        }
        if not ids:
            return empty

        tool_counts: dict[str, int] = {jid: 0 for jid in ids}
        for row in (
            StreamMessage.objects.filter(
                job_id__in=ids, message_type=StreamMessageType.TOOL
            )
            .values("job_id")
            .annotate(n=Count("id"))
        ):
            tool_counts[str(row["job_id"])] = int(row["n"] or 0)

        qs = (
            StreamMessage.objects.filter(
                job_id__in=ids, message_type__in=_COMMAND_TYPES
            )
            .order_by("-id")
            .values("job_id", "message_type", "content", "metadata")[
                : max(80, per_job * len(ids) * 4)
            ]
        )
        buckets: dict[str, list[str]] = {jid: [] for jid in ids}
        for row in qs:
            jid = str(row["job_id"])
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            text = display_command(meta, str(row.get("content") or ""))
            if not text:
                continue
            snippet = text[:160]
            is_tool = row["message_type"] == StreamMessageType.TOOL
            if is_tool:
                # Tools always win a slot (graph command snippets).
                existing = {c.lower() for c in buckets[jid]}
                if snippet.lower() not in existing:
                    buckets[jid].insert(0, snippet)
                    buckets[jid] = buckets[jid][:per_job]
                continue
            if len(buckets[jid]) >= per_job:
                continue
            buckets[jid].append(snippet)

        out: dict[str, dict] = {}
        for jid in ids:
            cmds = buckets.get(jid) or []
            seen: set[str] = set()
            uniq: list[str] = []
            for c in cmds:
                key = c.lower()
                if key in seen:
                    continue
                seen.add(key)
                uniq.append(c)
            out[jid] = {
                "commands": uniq[:per_job],
                "last_command": uniq[0] if uniq else "",
                "tool_calls": tool_counts.get(jid, 0),
            }
        return out

    @classmethod
    def _annotate_activity(cls, agents: list[dict], activity: dict[str, dict]) -> None:

        for agent in agents:
            info = activity.get(str(agent.get("id") or ""), {})
            stream_cmds = list(info.get("commands") or [])
            prior_cmds = list(agent.get("commands") or [])
            last = str(info.get("last_command") or "")
            # Prefer live tool stream over planner dry-run commands for the editor.
            agent["commands"] = stream_cmds or prior_cmds
            agent["last_command"] = last
            agent["tool_calls"] = int(info.get("tool_calls") or 0)
            agent["operator_command"] = pick_agent_command(
                last,
                *stream_cmds,
                *prior_cmds,
                str(agent.get("operator_command") or ""),
            )
            kids = agent.get("subagents") or []
            if kids:
                cls._annotate_activity(kids, activity)

    @classmethod
    def project_status_payload(cls, 
        project: Project,
        *,
        objectives: list[Objective] | None = None,
        jobs: list[Job] | None = None,
    ) -> dict:
        objs = objectives if objectives is not None else list(project.objectives.all())
        job_list = jobs if jobs is not None else list(project.jobs.all())
        return {
            "status": project.status,
            "completed_at": (
                project.completed_at.isoformat() if project.completed_at else ""
            ),
            "sandbox": cls.sandbox_payload(project),
            "progress": cls.progress_payload(objectives=objs, jobs=job_list),
            "phase": cls.active_phase(objs),
        }

    @classmethod
    def for_project(cls, project: Project, *, jobs_limit: int) -> dict:
        jobs = list(
            project.jobs.select_related("parent", "objective").all()[:jobs_limit]
        )
        objectives = list(project.objectives.all())
        agents = cls.agents_tree(jobs)
        activity = cls.recent_activity_by_job([str(j.id) for j in jobs])
        cls._annotate_activity(agents, activity)

        next_obj = ObjectiveScheduler().next_ready(project)

        return {
            "project": cls.project_status_payload(project, objectives=objectives, jobs=jobs),
            "agents": agents,
            "roles_used": cls.roles_used_from_jobs(jobs),
            "jobs": [cls.job_payload(j) for j in jobs],
            "objectives": [cls.objective_payload(o) for o in objectives],
            "reports": reports_payload(str(project.id)),
            "next_objective_ready": next_obj is not None,
            "pending_inputs": pending_operator_prompts(project),
            # Catalog/role-driven CLI + capability names for command detection UI.
            "command_hints": {
                "cli": cli_names_payload(),
                "capabilities": capability_names_payload(),
            },
        }
