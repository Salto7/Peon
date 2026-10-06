"""Objective scheduling — Peontester-style per-objective readiness."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone as dj_tz

from peon.projects.models import (
    Job,
    JobLifecycle,
    JobStatus,
    KillChainPhase,
    Objective,
    ObjectiveStatus,
    Project,
    ProjectStatus,
)
from orchestrator.utils.commands import normalize_objective_commands
from orchestrator.utils.paths import format_roe_block
from peon.projects.tasks import enqueue_job
from peon.projects.workspaces import project_workspace_dir, safe_workspace_key


class ObjectiveScheduler:
    """Gate and spawn work one objective at a time."""

    def dependencies_met(self, objective: Objective) -> bool:
        deps = objective.depends_on.all()
        if not deps.exists():
            return True
        return all(d.status == ObjectiveStatus.COMPLETED for d in deps)

    def next_ready(self, project: Project | None) -> Objective | None:
        """Next objective that should get a Job.

        Includes ``in_progress`` rows with no active Job (PM "release" without
        enqueue, or worker crash) so the pipeline cannot stall forever.
        """
        if project is None:
            return None
        for obj in project.objectives.order_by("seq", "created_at"):
            if obj.status == ObjectiveStatus.IN_PROGRESS:
                if self.has_active_job(obj):
                    # Wait for the running specialist before advancing.
                    return None
                # Orphaned release — fall through and (re)enqueue.
            elif obj.status != ObjectiveStatus.PENDING:
                continue
            if not self.dependencies_met(obj):
                continue
            if not (obj.role_id or "").strip():
                self.mark(
                    obj,
                    ObjectiveStatus.BLOCKED,
                    reason=(
                        "No role_id — assign a CrewAI role or replan"
                    ),
                )
                continue
            return obj
        return None

    def mark(
        self,
        objective: Objective,
        status: str,
        *,
        reason: str = "",
    ) -> Objective:
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
        objective.save(update_fields=fields)
        return objective

    def roles_for(self, objective: Objective) -> list[str]:
        """Primary role id for the first Job (single id; not a peer list)."""
        hint = (objective.role_id or "").strip()
        if not hint:
            return []
        # If planners historically comma-joined, take the first as primary only.
        primary = hint.replace(";", ",").split(",")[0].strip()
        return [primary] if primary else []

    def build_brief(self, project: Project, objective: Objective) -> str:
        from peon.projects.workspaces import (
            format_workspace_artifact_index,
            project_workspace_dir,
        )

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
        # Artifact index only for reporting roles (keeps specialist briefs small).
        role_id = (objective.role_id or "").strip()
        wants_index = False
        if role_id:
            try:
                from orchestrator.crew.roles.registry import RoleRegistry

                role = RoleRegistry.shared().get(role_id)
                caps = set(role.capabilities or ()) if role else set()
                wants_index = bool(role and ("report" in caps or role_id == "analyzer"))
            except Exception:
                wants_index = role_id == "analyzer"
        if wants_index:
            try:
                ws = project_workspace_dir(str(project.id), create=False)
                index = format_workspace_artifact_index(ws)
            except Exception:
                index = ""
            if index:
                parts.extend(
                    [
                        "",
                        "## Workspace artifacts from prior agents",
                        "Read these with list_workspace_artifacts / "
                        "read_workspace_artifact (do not re-scan to rediscover them):",
                        index,
                    ]
                )
        return "\n".join(parts)

    def has_active_job(self, objective: Objective) -> bool:
        return objective.jobs.exclude(
            status__in={
                JobStatus.COMPLETED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            }
        ).exists()

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
    ) -> Job | None:
        """Spawn one Solo Job for an objective. No-op if deps unmet or job already active."""
        if objective.status == ObjectiveStatus.CANCELLED:
            return None
        if not self.dependencies_met(objective) and objective.status == ObjectiveStatus.PENDING:
            return None
        if self.has_active_job(objective):
            return objective.jobs.exclude(
                status__in={
                    JobStatus.COMPLETED,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                }
            ).order_by("-created_at").first()

        preferred = (
            list(role_ids) if role_ids is not None else self.roles_for(objective)
        )
        solo = preferred[:1]
        if not solo:
            self.mark(
                objective,
                ObjectiveStatus.BLOCKED,
                reason=(
                    "No role_id — assign a CrewAI role (osint-expert, …) "
                    "or replan"
                ),
            )
            return None
        ws = safe_workspace_key(workspace_id or str(project.id))
        project_workspace_dir(ws)
        job = Job.objects.create(
            title=f"OBJ-{objective.seq}: {objective.title}"[:255],
            description=self.build_brief(project, objective),
            lifecycle=JobLifecycle.LONG,
            status=JobStatus.PENDING,
            role_ids=solo,
            project=project,
            objective=objective,
            plan_text=plan_text or "",
            plan_path=plan_path or "plans/latest.md",
            workspace_id=ws,
        )
        if objective.status == ObjectiveStatus.PENDING:
            self.mark(objective, ObjectiveStatus.IN_PROGRESS)
        if enqueue:
            enqueue_job(str(job.id))
        return job

    def enqueue_peer_jobs(
        self,
        project: Project,
        objective: Objective,
        peers: list[dict] | list[str],
        *,
        plan_text: str = "",
        workspace_id: str | None = None,
        parent: Job | None = None,
    ) -> list[Job]:
        """Enqueue specialist Jobs on the same objective (context-driven peers).

        ``peers`` items are dicts ``{title, description, role_id}`` or bare
        bare role id strings. Concurrent peers are allowed while other Jobs
        on the objective are still running.
        """
        created: list[Job] = []
        if objective.status == ObjectiveStatus.CANCELLED:
            return created
        ws = safe_workspace_key(workspace_id or str(project.id))
        project_workspace_dir(ws)
        primary = (self.roles_for(objective) or [""])[0]
        base_brief = self.build_brief(project, objective)
        for raw in peers:
            if isinstance(raw, str):
                role = raw.strip()
                title = f"OBJ-{objective.seq}/{role}: {objective.title}"[:255]
                description = base_brief
            elif isinstance(raw, dict):
                role = str(raw.get("role_id") or primary or "").strip()
                title = str(raw.get("title") or role or objective.title).strip()[:255]
                focus = str(raw.get("description") or "").strip()
                description = (
                    f"{base_brief}\n\n## Specialist focus\n{focus}" if focus else base_brief
                )
            else:
                continue
            if not role and not title:
                continue
            job = Job.objects.create(
                title=title or f"OBJ-{objective.seq}: {objective.title}"[:255],
                description=description,
                lifecycle=JobLifecycle.LONG,
                status=JobStatus.PENDING,
                role_ids=[role] if role else list(self.roles_for(objective)[:1]),
                project=project,
                objective=objective,
                parent=parent,
                plan_text=plan_text or "",
                plan_path="plans/latest.md",
                workspace_id=ws,
            )
            if objective.status == ObjectiveStatus.PENDING:
                self.mark(objective, ObjectiveStatus.IN_PROGRESS)
            enqueue_job(str(job.id))
            created.append(job)
        return created

    def enqueue_next(self, project: Project | None) -> Job | None:
        """Create+enqueue a job for the next ready objective (if any)."""
        if project is None:
            return None
        project.refresh_from_db()
        if project.status in {ProjectStatus.PAUSED, ProjectStatus.CANCELLED}:
            return None
        if project.status in {
            ProjectStatus.FINISHED,
            ProjectStatus.FINISHED_WITH_ERRORS,
        }:
            project.status = ProjectStatus.ACTIVE
            project.completed_at = None
            project.save(update_fields=["status", "completed_at", "updated_at"])
        obj = self.next_ready(project)
        if obj is None:
            return None
        plan_text = (
            project.jobs.order_by("-created_at").values_list("plan_text", flat=True).first()
            or ""
        )
        return self.create_run(project, obj, plan_text=plan_text, workspace_id=str(project.id))


def _normalize_phase(value: object) -> str:
    text = str(value or "").strip().lower()
    allowed = {c.value for c in KillChainPhase}
    return text if text in allowed else KillChainPhase.RECON


def _default_commands(objective: dict) -> list[str]:
    """No synthetic tool-call wrappers — specialists fill real shell CLIs."""
    del objective
    return []


class ObjectivePlanSync:
    """Replace/update project objectives from planner JSON (Peontester sync_from_plan)."""

    def __init__(self, scheduler: ObjectiveScheduler | None = None) -> None:
        self._sched = scheduler or ObjectiveScheduler()

    @transaction.atomic
    def sync(
        self,
        project: Project,
        objectives_payload: list[dict],
        *,
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
                obj = Objective.objects.create(
                    project=project,
                    seq=int(item.get("seq") or i),
                    title=title,
                    description=description,
                    phase=phase,
                    mitre_techniques=[str(m).strip() for m in mitre if str(m).strip()][:12],
                    acceptance_criteria=acceptance,
                    status=ObjectiveStatus.PENDING,
                    role_id=role_id,
                    profile_suggestion=profile,
                    commands=cmds or _default_commands(item),
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
                    obj.status = ObjectiveStatus.PENDING
                    obj.blocked_reason = ""
                    obj.completed_at = None
                    obj.started_at = None
                obj.save()
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
                old.save(update_fields=["status", "blocked_reason", "updated_at"])

        return list(project.objectives.exclude(status=ObjectiveStatus.CANCELLED).order_by("seq"))
