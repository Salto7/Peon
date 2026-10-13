"""Project control plane: Project, RoE, Objective, Job, Finding.

Engagement agents are CrewAI roles from the platform role store + skill affinity.
Job.role_ids holds role catalog ids; skills are the execution units.
"""

from __future__ import annotations

import uuid

from django.db import models


class ProjectScopedModel(models.Model):
    """Mixin: persist engagement rows into /workspaces/<project>/db.sqlite3."""

    class Meta:
        abstract = True

    def _project_scope_id(self):
        pid = getattr(self, "project_id", None)
        if pid:
            return pid
        job = getattr(self, "job", None)
        if job is not None and getattr(job, "project_id", None):
            return job.project_id
        return getattr(self, "job_id", None) and None

    def save(self, *args, **kwargs):
        pid = getattr(self, "project_id", None)
        if not pid:
            # Prefer the related Job instance (create(job=…)) before fan-out get_job.
            job = getattr(self, "job", None)
            if job is not None and getattr(job, "project_id", None):
                pid = job.project_id
            else:
                job_id = getattr(self, "job_id", None)
                if job_id:
                    from peon.projects.db import get_job

                    try:
                        pid = get_job(job_id).project_id
                    except Exception:
                        pid = None
            if pid:
                from peon.projects.db import ensure_project_database, using_project

                alias = ensure_project_database(pid)
                # Force: Manager.create() passes using=router.db (often default).
                kwargs["using"] = alias
                with using_project(pid):
                    return super().save(*args, **kwargs)
            return super().save(*args, **kwargs)
        from peon.projects.db import ensure_project_database, using_project

        alias = ensure_project_database(pid)
        kwargs["using"] = alias
        with using_project(pid):
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pid = getattr(self, "project_id", None)
        if pid:
            from peon.projects.db import ensure_project_database, using_project

            alias = ensure_project_database(pid)
            kwargs["using"] = alias
            with using_project(pid):
                return super().delete(*args, **kwargs)
        return super().delete(*args, **kwargs)


class ProjectStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    FINISHED = "finished", "Finished"
    FINISHED_WITH_ERRORS = "finished_with_errors", "Finished with errors"
    CANCELLED = "cancelled", "Cancelled"


class SandboxRuntime(models.TextChoices):
    """Operator pick at project create. Ids match agent_runtime registry keys."""

    SANDBOX = "sandbox", "Sandbox"
    OPENSHELL = "openshell", "OpenShell"

    @classmethod
    def resolve(cls, raw: str | None) -> str:
        value = (raw or "").strip().lower()
        if value in cls.values:
            return value
        return cls.SANDBOX


class ProjectKind(models.TextChoices):
    """Engagement duration model."""

    TIMED = "timed", "Timed"
    DURABLE = "durable", "Durable"

    @classmethod
    def resolve(cls, raw: str | None) -> str:
        value = (raw or "").strip().lower()
        if value in cls.values:
            return value
        return cls.TIMED


class KillChainPhase(models.TextChoices):
    RECON = "recon", "Reconnaissance"
    INITIAL_ACCESS = "initial-access", "Initial access"
    POST_EXPLOIT = "post-exploit", "Post-exploitation"
    REPORTING = "reporting", "Reporting"


class ObjectiveStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    IN_PROGRESS = "in_progress", "In progress"
    COMPLETED = "completed", "Completed"
    BLOCKED = "blocked", "Blocked"
    CANCELLED = "cancelled", "Cancelled"


class FindingSeverity(models.TextChoices):
    INFO = "info", "Info"
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    CRITICAL = "critical", "Critical"


class FindingStatus(models.TextChoices):
    OPEN = "open", "Open"
    CONFIRMED = "confirmed", "Confirmed"
    FALSE_POSITIVE = "false_positive", "False positive"
    FIXED = "fixed", "Fixed"
    ACCEPTED = "accepted", "Accepted risk"


class Project(models.Model):
    """Platform registry row for an engagement (not the project SQLite file).

    Engagement data (RoE, jobs, findings, …) lives in
    ``/workspaces/<id>/db.sqlite3``. Peon list/home/status use this row only.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    summary = models.TextField(
        blank=True,
        help_text="Operator brief — what success looks like.",
    )
    status = models.CharField(
        max_length=32,
        choices=ProjectStatus.choices,
        default=ProjectStatus.ACTIVE,
    )
    sandbox_runtime = models.CharField(
        max_length=32,
        choices=SandboxRuntime.choices,
        default=SandboxRuntime.SANDBOX,
        help_text="Chosen at project create. Sandbox is Docker; OpenShell is the locked runtime.",
    )
    kind = models.CharField(
        max_length=16,
        choices=ProjectKind.choices,
        default=ProjectKind.TIMED,
        help_text="timed = finite engagement; durable = 24/7 continuous project.",
    )
    plan_accepted = models.BooleanField(
        default=True,
        help_text="False until operator accepts the durable plan graph (timed defaults True).",
    )
    focus_tags = models.JSONField(
        default=list,
        blank=True,
        help_text="Soft RoleRouter preferred tags (domains/techniques).",
    )
    crew_flow_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="CrewAI project Flow / crew run id (Peon-crewAI).",
    )
    crew_status = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Crew run status: running|paused|awaiting_feedback|stopped|done.",
    )
    # Denormalized from project DB so Peon chrome never opens project SQLite.
    running_jobs = models.PositiveIntegerField(default=0)
    failed_jobs = models.PositiveIntegerField(default=0)
    open_prompts = models.PositiveIntegerField(default=0)
    last_error_summary = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.title} ({self.status})"

    def save(self, *args, **kwargs):
        # Registry writes always go to platform unless explicitly using=project_*.
        using = kwargs.get("using")
        if using is None and not str(getattr(self._state, "db", "") or "").startswith(
            "project_"
        ):
            kwargs["using"] = "default"
        result = super().save(*args, **kwargs)
        if kwargs.get("using", getattr(self._state, "db", "default")) == "default":
            try:
                from peon.projects.db import mirror_project_row

                mirror_project_row(self.pk)
            except Exception:
                pass
        return result

    def db_alias(self) -> str:
        from peon.projects.db import alias_for, ensure_project_database

        return ensure_project_database(self.pk)

    def _scoped(self, model: type):
        from peon.projects.db import ProjectRelation

        return ProjectRelation(model, self)

    @property
    def jobs(self):
        from peon.projects.models import Job

        return self._scoped(Job)

    @property
    def objectives(self):
        from peon.projects.models import Objective

        return self._scoped(Objective)

    @property
    def findings(self):
        from peon.projects.models import Finding

        return self._scoped(Finding)

    @property
    def schedules(self):
        from peon.projects.models import ProjectSchedule

        return self._scoped(ProjectSchedule)

    @property
    def operator_prompts(self):
        from peon.projects.models import OperatorPrompt

        return self._scoped(OperatorPrompt)

    @property
    def checkpoints(self):
        from peon.projects.models import JobCheckpoint

        return self._scoped(JobCheckpoint)

    @property
    def agent_messages(self):
        from peon.projects.models import AgentMessage

        return self._scoped(AgentMessage)

    @property
    def roe(self):
        from peon.projects.models import RulesOfEngagement

        alias = self.db_alias()
        return RulesOfEngagement.objects.using(alias).filter(pk=self.pk).first()

    @roe.setter
    def roe(self, value) -> None:
        return None

    @property
    def asset_graph(self):
        from peon.projects.models import AssetGraph

        alias = self.db_alias()
        return AssetGraph.objects.using(alias).filter(pk=self.pk).first()


class EngagementRole(models.Model):
    """Platform-global engagement role identity (Lab-editable).

    Skill membership comes from affinity sidecars, not this row.
    """

    role_id = models.CharField(max_length=128, primary_key=True)
    kind = models.CharField(
        max_length=32,
        help_text="engagement-pm | analyzer | technical",
    )
    aliases = models.JSONField(default=list, blank=True)
    protected = models.BooleanField(
        default=False,
        help_text="Bookend roles that Lab must not delete.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Engagement role"
        verbose_name_plural = "Engagement roles"
        ordering = ["role_id"]

    def __str__(self) -> str:
        return f"{self.role_id} ({self.kind})"


class RoleSkillMemory(models.Model):
    """Platform-global observed role → skill usage (affinity is the allowlist)."""

    role_id = models.CharField(max_length=128, primary_key=True)
    skills = models.JSONField(default=list, blank=True)
    counts = models.JSONField(default=dict, blank=True)
    last_source = models.CharField(max_length=64, blank=True, default="")
    last_project_id = models.CharField(max_length=64, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Role skill memory"
        verbose_name_plural = "Role skill memory"

    def __str__(self) -> str:
        return f"{self.role_id}: {len(self.skills or [])} skills"


class RulesOfEngagement(ProjectScopedModel):
    """Scope and safety rules — values authorize; type is an optional hint."""

    project = models.OneToOneField(
        Project,
        on_delete=models.CASCADE,
        related_name="+",
        primary_key=True,
        db_constraint=False,
    )
    in_scope = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'Authorized targets as {"type","value"} (type is a soft hint). '
            "Plain strings are coerced on read."
        ),
    )
    exclusions = models.JSONField(
        default=list,
        blank=True,
        help_text="Explicitly out-of-scope targets (same shape as in_scope).",
    )
    seed = models.JSONField(
        default=list,
        blank=True,
        help_text="Engagement seeds / intent clues (not yet authorized attack targets).",
    )
    candidates = models.JSONField(
        default=list,
        blank=True,
        help_text="Discovered assets awaiting promote-to-scope.",
    )
    testing_window_notes = models.TextField(blank=True)
    authorization_note = models.TextField(blank=True)
    abort_triggers = models.TextField(blank=True)
    opsec_notes = models.TextField(blank=True)
    contacts = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Rules of engagement"
        verbose_name_plural = "Rules of engagement"

    def __str__(self) -> str:
        return f"Rules of Engagement for {self.project_id}"


class Objective(ProjectScopedModel):
    """Kill-chain step under a project (maps to orchestrator project-plan objectives)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="+",
        db_constraint=False,
    )
    seq = models.PositiveIntegerField(default=1)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    phase = models.CharField(
        max_length=32,
        choices=KillChainPhase.choices,
        default=KillChainPhase.RECON,
    )
    mitre_techniques = models.JSONField(default=list, blank=True)
    depends_on = models.ManyToManyField(
        "self",
        symmetrical=False,
        blank=True,
        related_name="dependents",
    )
    acceptance_criteria = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=ObjectiveStatus.choices,
        default=ObjectiveStatus.PENDING,
    )
    blocked_reason = models.TextField(blank=True)
    role_id = models.CharField(
        max_length=128,
        blank=True,
        help_text="CrewAI role id from the platform role store (e.g. osint-expert).",
    )
    skill_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="Selected skill ids from the role's skill group (subset).",
    )
    profile_suggestion = models.CharField(
        max_length=128,
        blank=True,
        help_text="Optional agent role (e.g. OSINT-lead).",
    )
    commands = models.JSONField(
        default=list,
        blank=True,
        help_text="Dry-run command strings from the planner (not executed here).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["project", "seq", "created_at"]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "phase"]),
        ]

    def __str__(self) -> str:
        return f"OBJ-{self.seq}: {self.title} ({self.status})"


class JobLifecycle(models.TextChoices):
    SHORT = "short", "Short"
    LONG = "long", "Long"


class JobStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    PAUSED = "paused", "Paused"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


TERMINAL_JOB_STATUSES = frozenset(
    {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
)

# Project is done (operator may still view; no new work without resume/replan).
TERMINAL_PROJECT_STATUSES = frozenset(
    {
        ProjectStatus.FINISHED,
        ProjectStatus.FINISHED_WITH_ERRORS,
        ProjectStatus.CANCELLED,
    }
)

# Operator/scheduler must not enqueue while paused or cancelled.
INACTIVE_PROJECT_STATUSES = frozenset(
    {ProjectStatus.PAUSED, ProjectStatus.CANCELLED}
)

# Finished (with or without errors) — durable stays ACTIVE instead of these.
FINISHED_PROJECT_STATUSES = frozenset(
    {ProjectStatus.FINISHED, ProjectStatus.FINISHED_WITH_ERRORS}
)


class Job(models.Model):
    """One agent run; optional Project / Objective link. role_ids → engagement roles."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    lifecycle = models.CharField(
        max_length=20,
        choices=JobLifecycle.choices,
        default=JobLifecycle.SHORT,
    )
    status = models.CharField(
        max_length=20,
        choices=JobStatus.choices,
        default=JobStatus.PENDING,
    )
    role_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="CrewAI role ids from the platform role store (RoleRegistry).",
    )
    skill_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="Selected skill ids for this job (subset of the role skill group).",
    )
    project = models.ForeignKey(
        Project,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
        db_constraint=False,
    )
    objective = models.ForeignKey(
        Objective,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="jobs",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
    )
    profile = models.CharField(max_length=128, blank=True)
    plan_text = models.TextField(blank=True)
    plan_path = models.CharField(max_length=1024, blank=True)
    workspace_id = models.CharField(max_length=64, blank=True)
    result = models.TextField(blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    resume_from_checkpoint = models.BooleanField(
        default=False,
        help_text="Next agent run should resume the CrewAI role/crew with steer context.",
    )

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if self.project_id:
            expected = str(self.project_id)
            if self.workspace_id != expected:
                self.workspace_id = expected
                update_fields = kwargs.get("update_fields")
                if update_fields is not None:
                    kwargs["update_fields"] = {*update_fields, "workspace_id"}
            from peon.projects.db import ensure_project_database, using_project

            alias = ensure_project_database(self.project_id)
            kwargs["using"] = alias
            with using_project(self.project_id):
                return super().save(*args, **kwargs)
        kwargs.setdefault("using", "default")
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.title} ({self.status})"


class JobDirectiveKind(models.TextChoices):
    STEER = "steer", "Steer"
    FOLLOWUP = "followup", "Follow-up"
    REPLAN = "replan", "Replan"


class JobDirective(ProjectScopedModel):
    """Operator instruction queued for a Job (consumed by the agent loop)."""

    id = models.BigAutoField(primary_key=True)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="directives")
    kind = models.CharField(
        max_length=16,
        choices=JobDirectiveKind.choices,
        default=JobDirectiveKind.STEER,
    )
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["job", "consumed_at"]),
        ]

    def __str__(self) -> str:
        preview = (self.content or "")[:40]
        return f"{self.kind}:{preview}"


class OperatorPromptKind(models.TextChoices):
    """Typed HITL reasons (structured — not scraped from free text)."""

    AGENT_ASK = "agent_ask", "Agent ask"
    BLOCKED_OBJECTIVE = "blocked_objective", "Blocked objective"
    FAILURE_EXHAUSTED = "failure_exhausted", "Failure replans exhausted"
    PLAN_CHANGE = "plan_change", "Plan change"


class OperatorPrompt(ProjectScopedModel):
    """Human-in-the-loop ask: agent/control-plane needs operator input."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="+",
        db_constraint=False,
    )
    job = models.ForeignKey(
        Job,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="operator_prompts",
    )
    kind = models.CharField(
        max_length=32,
        choices=OperatorPromptKind.choices,
        default=OperatorPromptKind.AGENT_ASK,
        help_text="agent_ask | blocked_objective | failure_exhausted | plan_change",
    )
    question = models.TextField()
    options = models.JSONField(default=list, blank=True)
    reply = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["project", "resolved_at"]),
            models.Index(fields=["kind", "resolved_at"]),
        ]

    def __str__(self) -> str:
        preview = (self.question or "")[:48]
        state = "open" if self.resolved_at is None else "done"
        return f"[{state}/{self.kind}] {preview}"


class ScheduleKind(models.TextChoices):
    CRON = "cron", "Cron"
    INTERVAL = "interval", "Interval"
    ONCE = "once", "Once"


class ProjectSchedule(ProjectScopedModel):
    """Durable cadence: enqueue Jobs on cron/interval without a second runtime."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="+",
        db_constraint=False,
    )
    objective = models.ForeignKey(
        Objective,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="schedules",
    )
    title = models.CharField(max_length=255, blank=True, default="")
    role_id = models.CharField(max_length=128, blank=True, default="")
    brief = models.TextField(
        blank=True,
        help_text="Job description / prompt override for scheduled runs.",
    )
    kind = models.CharField(
        max_length=16,
        choices=ScheduleKind.choices,
        default=ScheduleKind.INTERVAL,
    )
    cron = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="5-field cron (minute hour day month weekday) when kind=cron.",
    )
    interval_seconds = models.PositiveIntegerField(
        default=0,
        help_text="Seconds between runs when kind=interval.",
    )
    enabled = models.BooleanField(default=True)
    skip_if_running = models.BooleanField(
        default=True,
        help_text="Skip run when a non-terminal Job already exists for this schedule.",
    )
    next_run_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_job = models.ForeignKey(
        Job,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["next_run_at", "created_at"]
        indexes = [
            models.Index(fields=["enabled", "next_run_at"]),
            models.Index(fields=["project", "enabled"]),
        ]

    def __str__(self) -> str:
        label = (self.title or self.role_id or "schedule").strip()
        return f"{label} ({self.kind})"


class StreamMessageType(models.TextChoices):
    LOG = "log", "Log"
    STDOUT = "stdout", "Stdout"
    STDERR = "stderr", "Stderr"
    TOOL = "tool", "Tool"
    THINKING = "thinking", "Thinking"
    RESULT = "result", "Result"
    ERROR = "error", "Error"
    STATUS = "status", "Status"


class StreamMessage(ProjectScopedModel):
    """One live stream line for a Job (Unix socket ingest and/or worker emit)."""

    id = models.BigAutoField(primary_key=True)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="messages")
    message_type = models.CharField(
        max_length=20,
        choices=StreamMessageType.choices,
        default=StreamMessageType.LOG,
    )
    content = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["job", "created_at"]),
        ]

    def __str__(self) -> str:
        preview = self.content[:60] + ("…" if len(self.content) > 60 else "")
        return f"[{self.message_type}] {preview}"


class RuntimeSettings(models.Model):
    """Singleton operator policy (pk=1). Values keyed like env names."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    values = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Runtime settings"
        verbose_name_plural = "Runtime settings"

    def __str__(self) -> str:
        return "Runtime settings"


class Finding(ProjectScopedModel):
    """Engagement discovery about a subject (any asset class) under a project."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="+",
        db_constraint=False,
    )
    objective = models.ForeignKey(
        Objective,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="findings",
    )
    job = models.ForeignKey(
        Job,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="findings",
    )
    seq = models.PositiveIntegerField(default=1)
    title = models.CharField(max_length=255)
    kind = models.CharField(
        max_length=32,
        default="observation",
        help_text="Free-form finding kind slug (role/LLM); not a closed enum.",
    )
    asset_type = models.CharField(
        max_length=32,
        default="",
        blank=True,
        help_text="Free-form asset/target type slug (role/LLM); not a closed enum.",
    )
    severity = models.CharField(
        max_length=20,
        choices=FindingSeverity.choices,
        default=FindingSeverity.INFO,
    )
    status = models.CharField(
        max_length=20,
        choices=FindingStatus.choices,
        default=FindingStatus.OPEN,
    )
    host = models.CharField(max_length=255, blank=True)
    service = models.CharField(max_length=128, blank=True)
    port = models.PositiveIntegerField(null=True, blank=True)
    protocol = models.CharField(max_length=16, blank=True)
    cve_id = models.CharField(max_length=64, blank=True)
    cwe_id = models.CharField(max_length=32, blank=True)
    mitre_techniques = models.JSONField(default=list, blank=True)
    description = models.TextField(blank=True)
    evidence = models.TextField(blank=True)
    evidence_path = models.CharField(
        max_length=1024,
        blank=True,
        help_text="Workspace provenance path only — not an engagement subject.",
    )
    subjects = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'Affected subjects: [{"type","value"}] (optional "relation"). '
            "evidence_path is never a subject."
        ),
    )
    remediation = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["project", "seq", "-severity", "created_at"]
        indexes = [
            models.Index(fields=["project", "severity"]),
            models.Index(fields=["project", "status"]),
        ]

    def __str__(self) -> str:
        return f"FIND-{self.seq}: {self.title}"


class AssetGraph(ProjectScopedModel):
    """Engagement asset graph (open types / relations).

    Types and relation labels are free-form slugs from producers (roles,
    findings, operator, tools). Not a closed taxonomy — new node/edge kinds
    appear in the UI without schema changes.
    """

    project = models.OneToOneField(
        Project,
        on_delete=models.CASCADE,
        related_name="+",
        primary_key=True,
        db_constraint=False,
    )
    assets = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'Assets: [{"id","type","value","key","bucket","source","props"?}]. '
            "type is a free-form slug."
        ),
    )
    relations = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'Relations: [{"id","rel","source","target","props"?}]. '
            "rel is a free-form slug."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"AssetGraph<{self.project_id}> {len(self.assets or [])} assets"


class JobCheckpoint(ProjectScopedModel):
    """Domain/context checkpoint written when a Job attempt ends (mandatory)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="checkpoints")
    project = models.ForeignKey(
        Project,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
        db_constraint=False,
    )
    objective = models.ForeignKey(
        Objective,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="checkpoints",
    )
    seq = models.PositiveIntegerField(default=1)
    reason = models.CharField(
        max_length=64,
        blank=True,
        default="job_finished",
        help_text="job_finished | resume | steer | scheduled_run",
    )
    context = models.JSONField(
        default=dict,
        blank=True,
        help_text="Structured domain context: status, roles, digest refs, artifacts.",
    )
    transcript_digest = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["job", "-seq", "-created_at"]
        indexes = [
            models.Index(fields=["job", "-seq"]),
            models.Index(fields=["project", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"CKPT-{self.seq} job={self.job_id}"


class AgentMessage(ProjectScopedModel):
    """In-process agent↔agent message (A2A-shaped; protocol adapter later)."""

    class MsgType(models.TextChoices):
        REQUEST = "request", "Request"
        INFORM = "inform", "Inform"
        HANDOFF = "handoff", "Handoff"
        CHALLENGE = "challenge", "Challenge"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="+",
        db_constraint=False,
    )
    objective = models.ForeignKey(
        Objective,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="agent_messages",
    )
    from_job = models.ForeignKey(
        Job, on_delete=models.CASCADE, related_name="messages_sent"
    )
    to_job = models.ForeignKey(
        Job,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="messages_received",
        help_text="Null = broadcast to other jobs on the same objective.",
    )
    msg_type = models.CharField(
        max_length=16, choices=MsgType.choices, default=MsgType.INFORM
    )
    body = models.TextField()
    artifact_refs = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["to_job", "consumed_at"]),
            models.Index(fields=["objective", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.msg_type}:{str(self.id)[:8]}"


class CatalogSkill(models.Model):
    """Platform skill registry row. File bodies live in ``CatalogFile``."""

    skill_id = models.CharField(max_length=128, primary_key=True)
    provider = models.CharField(max_length=32, default="peon")
    description = models.TextField(blank=True, default="")
    body = models.TextField(blank=True, default="")
    manifest = models.JSONField(default=dict, blank=True)
    setup = models.JSONField(null=True, blank=True)
    content_hash = models.CharField(max_length=64, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["skill_id"]

    def __str__(self) -> str:
        return self.skill_id


class CatalogMcp(models.Model):
    """Platform MCP server spec. Env values are never stored here."""

    mcp_id = models.CharField(max_length=128, primary_key=True)
    description = models.TextField(blank=True, default="")
    transport = models.CharField(max_length=16, default="stdio")
    command = models.CharField(max_length=256)
    args = models.JSONField(default=list, blank=True)
    install = models.JSONField(null=True, blank=True)
    env_schema = models.JSONField(default=list, blank=True)
    enabled = models.BooleanField(default=True)
    content_hash = models.CharField(max_length=64, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["mcp_id"]

    def __str__(self) -> str:
        return self.mcp_id


class CatalogFile(models.Model):
    """Text file belonging to a skill or MCP server."""

    owner_kind = models.CharField(max_length=16)
    owner_id = models.CharField(max_length=128)
    path = models.CharField(max_length=512)
    body = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner_kind", "owner_id", "path"],
                name="catalog_file_owner_path",
            )
        ]
        indexes = [
            models.Index(fields=["owner_kind", "owner_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.owner_kind}:{self.owner_id}:{self.path}"


class CatalogBinding(models.Model):
    """Role mapping for a skill or MCP server."""

    role_id = models.CharField(max_length=128)
    owner_kind = models.CharField(max_length=16)
    owner_id = models.CharField(max_length=128)
    score = models.FloatField(default=1.0)
    lead = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["role_id", "owner_kind", "owner_id"],
                name="catalog_binding_role_owner",
            )
        ]
        indexes = [
            models.Index(fields=["owner_kind", "owner_id"]),
            models.Index(fields=["role_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.role_id}->{self.owner_kind}:{self.owner_id}"


class ProjectMcpConfig(ProjectScopedModel):
    """Per-project env values for an MCP server. Names live on ``CatalogMcp``."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="+",
        db_constraint=False,
    )
    mcp_id = models.CharField(max_length=128)
    env = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "mcp_id"],
                name="project_mcp_config_unique",
            )
        ]

    def __str__(self) -> str:
        return f"{self.project_id}:{self.mcp_id}"
