"""Project control plane: Project, RoE, Objective, Job, Finding.

Filesystem skills stay in orchestrator SkillRegistry — Job.skill_names holds ids only.
"""

from __future__ import annotations

import uuid

from django.db import models


class ProjectStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    FINISHED = "finished", "Finished"
    FINISHED_WITH_ERRORS = "finished_with_errors", "Finished with errors"
    CANCELLED = "cancelled", "Cancelled"


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
    """Engagement container: RoE, objectives, findings, jobs."""

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
    focus_tags = models.JSONField(
        default=list,
        blank=True,
        help_text="Soft SkillRouter preferred_tags (domains/techniques).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.title} ({self.status})"


class RulesOfEngagement(models.Model):
    """Scope and safety rules — values authorize; type is an optional hint."""

    project = models.OneToOneField(
        Project,
        on_delete=models.CASCADE,
        related_name="roe",
        primary_key=True,
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


class Objective(models.Model):
    """Kill-chain step under a project (maps to orchestrator project-plan objectives)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="objectives",
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
    skill_suggestion = models.CharField(
        max_length=128,
        blank=True,
        help_text="Catalog skill id from SkillRegistry (e.g. network-scanner).",
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


class Job(models.Model):
    """One agent run; optional Project / Objective link. skill_names → SkillRegistry."""

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
    skill_names = models.JSONField(
        default=list,
        blank=True,
        help_text="Catalog skill ids (SkillRegistry / SkillRouter).",
    )
    project = models.ForeignKey(
        Project,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="jobs",
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

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.title} ({self.status})"


class JobDirectiveKind(models.TextChoices):
    STEER = "steer", "Steer"
    FOLLOWUP = "followup", "Follow-up"


class JobDirective(models.Model):
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


class OperatorPrompt(models.Model):
    """Human-in-the-loop ask: agent/control-plane needs operator input."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="operator_prompts",
    )
    job = models.ForeignKey(
        Job,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="operator_prompts",
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
        ]

    def __str__(self) -> str:
        preview = (self.question or "")[:48]
        state = "open" if self.resolved_at is None else "done"
        return f"[{state}] {preview}"


class StreamMessageType(models.TextChoices):
    LOG = "log", "Log"
    STDOUT = "stdout", "Stdout"
    STDERR = "stderr", "Stderr"
    TOOL = "tool", "Tool"
    THINKING = "thinking", "Thinking"
    RESULT = "result", "Result"
    ERROR = "error", "Error"
    STATUS = "status", "Status"


class StreamMessage(models.Model):
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


class Finding(models.Model):
    """Engagement discovery about a subject (any asset class) under a project."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="findings",
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
        help_text="Free-form finding kind slug (skill/LLM); not a closed enum.",
    )
    asset_type = models.CharField(
        max_length=32,
        default="",
        blank=True,
        help_text="Free-form asset/target type slug (skill/LLM); not a closed enum.",
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
    evidence_path = models.CharField(max_length=1024, blank=True)
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


class AssetGraph(models.Model):
    """Engagement asset graph (open types / relations).

    Types and relation labels are free-form slugs from producers (skills,
    findings, operator, tools). Not a closed taxonomy — new node/edge kinds
    appear in the UI without schema changes.
    """

    project = models.OneToOneField(
        Project,
        on_delete=models.CASCADE,
        related_name="asset_graph",
        primary_key=True,
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