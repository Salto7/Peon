"""Objective scheduling — Peontester-style per-objective readiness (not per-skill)."""

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
from peon.projects.tasks import enqueue_job
from peon.projects.targets import format_targets
from peon.projects.workspaces import project_workspace_dir, safe_workspace_key


class ObjectiveScheduler:
    """Gate and spawn work one objective at a time."""

    def dependencies_met(self, objective: Objective) -> bool:
        deps = objective.depends_on.all()
        if not deps.exists():
            return True
        return all(d.status == ObjectiveStatus.COMPLETED for d in deps)

    def next_ready(self, project: Project | None) -> Objective | None:
        if project is None:
            return None
        for obj in project.objectives.order_by("seq", "created_at"):
            if obj.status != ObjectiveStatus.PENDING:
                continue
            if not self.dependencies_met(obj):
                continue
            if not (obj.skill_suggestion or "").strip():
                self.mark(
                    obj,
                    ObjectiveStatus.BLOCKED,
                    reason=(
                        "No skill_suggestion — assign a catalog skill or replan"
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

    def skills_for(self, objective: Objective) -> list[str]:
        hint = (objective.skill_suggestion or "").strip()
        return [hint] if hint else []

    def build_brief(self, project: Project, objective: Objective) -> str:
        roe = getattr(project, "roe", None)
        in_scope = ", ".join(format_targets(roe.in_scope if roe else [])) or "(empty)"
        excl = ", ".join(format_targets(roe.exclusions if roe else [])) or "(none)"
        seed = (
            ", ".join(format_targets(getattr(roe, "seed", None) if roe else []))
            or "(none)"
        )
        return (
            f"Execute project objective OBJ-{objective.seq}: {objective.title}\n"
            f"Phase: {objective.phase}\n"
            f"Description: {objective.description or '(none)'}\n"
            f"Acceptance criteria: {objective.acceptance_criteria or '(none)'}\n\n"
            f"Project: {project.title}\n"
            f"In-scope (authorized assets; type is optional hint): {in_scope}\n"
            f"Exclusions: {excl}\n"
            f"Seed / intent: {seed}\n"
            "Type labels (ip:, file:, malware:, …) are hints only — skills interpret values. "
            "Discoveries go to candidates/findings — not authorized until promoted "
            "into in-scope. "
            "record_finding is for engagement discoveries about subjects (any asset "
            "class) with evidence — not job/objective/agent progress. "
            "Write evidence under workspace/; curated notes under findings/<skill>.md. "
            "Do not write findings/report.md unless this objective is the analyzer."
        )

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
    ) -> Job | None:
        """Spawn one Job for one ready objective. No-op if deps unmet or job already active."""
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

        skills = self.skills_for(objective)
        if not skills:
            self.mark(
                objective,
                ObjectiveStatus.BLOCKED,
                reason=(
                    "No skill_suggestion — assign a catalog skill or replan"
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
            skill_names=skills,
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
    skill_id = str(objective.get("skill_suggestion") or "").strip()
    if not skill_id:
        return []
    return [
        "sandbox_setup()",
        f'run_skill_script("{skill_id}", "scripts/run.py", command="<fill from objective>")',
    ]


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
            cmds = [str(c).strip() for c in (item.get("commands") or []) if str(c).strip()]
            mitre = item.get("mitre") or item.get("mitre_techniques") or []
            if not isinstance(mitre, list):
                mitre = []
            skill = str(item.get("skill_suggestion") or "").strip()
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
                    skill_suggestion=skill,
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
                obj.skill_suggestion = skill or obj.skill_suggestion
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
