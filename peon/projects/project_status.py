"""Project status JSON for the ops console (agents, skills, progress)."""


from __future__ import annotations

from peon.projects.models import (
    TERMINAL_JOB_STATUSES,
    Job,
    Objective,
    ObjectiveStatus,
    Project,
    StreamMessage,
    StreamMessageType,
)
from peon.projects.sandbox import ProjectSandbox
from peon.projects.catalog import SkillCards

# Prefer tool lines; fall back to recent log/status for graph snippets.
_COMMAND_TYPES = (
    StreamMessageType.TOOL,
    StreamMessageType.LOG,
    StreamMessageType.STATUS,
    StreamMessageType.RESULT,
)



class ProjectStatus:
    """Build JSON for the project ops console (agents, skills, progress)."""

    @classmethod
    def job_payload(cls, job: Job) -> dict:
        return {
            "id": str(job.id),
            "title": job.title,
            "status": job.status,
            "profile": job.profile or "",
            "skill_names": job.skill_names or [],
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
        from peon.projects.agent_commands import pick_agent_command

        description = (job.description or "").strip()
        # Edit/re-run targets agent-emitted CLI/tool lines — never the full brief.
        operator_command = pick_agent_command(*obj_cmds)
        return {
            "id": str(job.id),
            "title": job.title,
            "status": job.status,
            "profile": job.profile or "",
            "skill_names": job.skill_names or [],
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
            "skill_suggestion": obj.skill_suggestion or "",
            "status": obj.status,
        }


    @classmethod
    def sandbox_payload(cls, project: Project) -> dict:
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
    def skills_used_from_jobs(cls, jobs: list[Job]) -> list[dict]:
        names: list[str] = []
        seen: set[str] = set()
        for job in jobs:
            for raw in job.skill_names or []:
                name = str(raw).strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                names.append(name)
        return SkillCards.for_names(names)


    @classmethod
    def agents_tree(cls, jobs: list[Job]) -> list[dict]:
        by_parent: dict[str, list[Job]] = {}
        roots: list[Job] = []
        for job in jobs:
            if job.parent_id:
                by_parent.setdefault(str(job.parent_id), []).append(job)
            else:
                roots.append(job)
        # Chronological / objective order (jobs queryset is newest-first).
        def _root_key(j: Job) -> tuple:
            obj = getattr(j, "objective", None)
            seq = obj.seq if obj is not None else 10**9
            created = j.created_at.timestamp() if j.created_at else 0
            return (seq, created)

        roots = sorted(roots, key=_root_key)
        for kids in by_parent.values():
            kids.sort(key=lambda j: j.created_at.timestamp() if j.created_at else 0)
        return [
            cls.agent_payload(root, subagents=by_parent.get(str(root.id), []), role="ROOT")
            for root in roots
        ]


    @classmethod
    def recent_activity_by_job(cls, 
        job_ids: list[str], *, per_job: int = 4
    ) -> dict[str, dict]:
        """Latest command snippets + tool-call counts keyed by job id."""
        from django.db.models import Count

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
            .values("job_id", "message_type", "content")[
                : max(80, per_job * len(ids) * 4)
            ]
        )
        buckets: dict[str, list[str]] = {jid: [] for jid in ids}
        for row in qs:
            jid = str(row["job_id"])
            text = str(row.get("content") or "").strip()
            if not text:
                continue
            is_tool = row["message_type"] == StreamMessageType.TOOL
            if is_tool:
                # Tools always win a slot (graph command snippets).
                existing = {c.lower() for c in buckets[jid]}
                if text.lower() not in existing:
                    buckets[jid].insert(0, text[:160])
                    buckets[jid] = buckets[jid][:per_job]
                continue
            if len(buckets[jid]) >= per_job:
                continue
            buckets[jid].append(text[:160])

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
        from peon.projects.agent_commands import pick_agent_command

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
        from peon.projects.objectives import ObjectiveScheduler
        from peon.projects.workspaces import reports_payload

        next_obj = ObjectiveScheduler().next_ready(project)
        return {
            "project": cls.project_status_payload(project, objectives=objectives, jobs=jobs),
            "agents": agents,
            "skills_used": cls.skills_used_from_jobs(jobs),
            "jobs": [cls.job_payload(j) for j in jobs],
            "objectives": [cls.objective_payload(o) for o in objectives],
            "reports": reports_payload(str(project.id)),
            "next_objective_ready": next_obj is not None,
        }
