"""Peon bridge for orchestrator.agent (settings + JobAgentBridge)."""

from __future__ import annotations

import json
from typing import Any

from django.utils import timezone as dj_tz

from orchestrator.agent import (
    AgentPorts,
    AgentRunConfig,
    agent_run_config_from_mapping,
)
from peon.projects.models import (
    TERMINAL_JOB_STATUSES,
    Finding,
    Job,
    JobDirective,
    JobDirectiveKind,
    JobLifecycle,
    JobStatus,
    Objective,
    ObjectiveStatus,
)
from peon.projects.streaming import emit_job_stream


def agent_run_config() -> AgentRunConfig:
    """Load agent governors from Peon Settings (DB) with .env defaults."""
    from peon.projects.runtime_settings import PeonSettings

    return agent_run_config_from_mapping(
        {
            "AGENT_MAX_FAILURE_REPLANS": PeonSettings.get_int(
                "AGENT_MAX_FAILURE_REPLANS", 2
            ),
            "AGENT_MAX_ITERATIONS": PeonSettings.get_int("AGENT_MAX_ITERATIONS", 40),
            "AGENT_MAX_SUBAGENTS": PeonSettings.get_int("AGENT_MAX_SUBAGENTS", 4),
            "AGENT_MAX_SUBAGENT_DEPTH": PeonSettings.get_int(
                "AGENT_MAX_SUBAGENT_DEPTH", 2
            ),
            "AGENT_RUNTIME_ENABLED": PeonSettings.get_bool(
                "AGENT_RUNTIME_ENABLED", True
            ),
        }
    )


class JobAgentBridge(AgentPorts):
    """Django-backed bridge: stream, spawn, findings, objectives for the job agent."""

    def __init__(self, job: Job) -> None:
        self._job = job

    def emit(
        self, message_type: str, content: str, *, metadata: dict[str, Any] | None = None
    ) -> None:
        emit_job_stream(self._job, message_type, content or "", metadata or {})

    def spawn_child(
        self, *, title: str, description: str, skill_names: list[str]
    ) -> str:
        cfg = agent_run_config()
        active = self._job.children.exclude(
            status__in=TERMINAL_JOB_STATUSES
        ).count()
        if active >= cfg.max_subagents:
            raise RuntimeError(f"Too many active subagents (max {cfg.max_subagents})")
        from peon.projects.tasks import enqueue_job

        child = Job.objects.create(
            title=(title or "subagent")[:255],
            description=description or "",
            lifecycle=JobLifecycle.LONG,
            status=JobStatus.PENDING,
            skill_names=list(skill_names or []),
            project_id=self._job.project_id,
            parent=self._job,
            workspace_id=self._job.workspace_id,
            plan_text=self._job.plan_text or "",
            plan_path=self._job.plan_path or "",
        )
        enqueue_job(child)
        return str(child.id)

    def wait_children(
        self, *, job_ids: list[str] | None = None, timeout_seconds: int = 600
    ) -> str:
        """Non-blocking status check (Phase 3B) — never holds the worker thread.

        Returns immediately with finished / still-running lists so the agent can
        continue or call again. Long sleeps deadlocked Dramatiq when threads were
        scarce; release-and-resume (3A) can replace this later.
        """
        del timeout_seconds  # reserved for future release-wait (3A)
        qs = self._job.children.all()
        if job_ids:
            qs = qs.filter(id__in=job_ids)
        kids = list(qs)
        if not kids:
            return "No subagents found for this job."
        done = [k for k in kids if k.status in TERMINAL_JOB_STATUSES]
        pending = [k for k in kids if k.status not in TERMINAL_JOB_STATUSES]
        if not pending:
            lines = [f"All {len(kids)} subagent(s) finished:"]
            for k in done:
                lines.append(f"- {k.title} ({k.id}) → {k.status}")
            return "\n".join(lines)
        self._job.refresh_from_db()
        if self._job.status in {JobStatus.CANCELLED, JobStatus.PAUSED}:
            return f"Parent job {self._job.status}; stop waiting."
        lines = [
            f"{len(done)} finished, {len(pending)} still running "
            f"(call wait_for_subagents again later; do not block):"
        ]
        for k in pending:
            lines.append(f"- {k.title} ({k.id}) → {k.status}")
        for k in done:
            lines.append(f"- {k.title} ({k.id}) → {k.status}")
        return "\n".join(lines)

    def list_objectives(self) -> str:
        if not self._job.project_id:
            return "No project on this job."
        rows = list(
            Objective.objects.filter(project_id=self._job.project_id).order_by("seq")
        )
        if not rows:
            return "No objectives."
        return "\n".join(
            f"OBJ-{o.seq} [{o.status}] {o.title} skill={o.skill_suggestion or '-'}"
            for o in rows
        )

    def update_objective_status(self, seq: int, status: str, note: str = "") -> str:
        if not self._job.project_id:
            return "No project on this job."
        obj = Objective.objects.filter(
            project_id=self._job.project_id, seq=int(seq)
        ).first()
        if obj is None:
            return f"Objective OBJ-{seq} not found."
        status = (status or "").strip().lower()
        valid = {c.value for c in ObjectiveStatus}
        if status not in valid:
            return f"Invalid status {status!r}; want one of {sorted(valid)}"
        obj.status = status
        if note:
            obj.blocked_reason = note[:2000]
        if status == ObjectiveStatus.COMPLETED:
            obj.completed_at = dj_tz.now()
        obj.save()
        if status == ObjectiveStatus.BLOCKED and self._job.project_id:
            try:
                from peon.projects.console_chat import create_operator_prompt

                reason = (note or obj.blocked_reason or "Objective blocked").strip()
                create_operator_prompt(
                    self._job.project,
                    f"OBJ-{obj.seq} {obj.title}: {reason}",
                    job=self._job,
                )
            except Exception:
                pass
        return f"OBJ-{obj.seq} → {obj.status}"

    def record_finding(self, **fields: Any) -> str:
        if not self._job.project_id:
            return "No project on this job."
        title = str(fields.get("title") or "").strip()
        if not title:
            return "Error: title required."
        from peon.projects.findings import FindingStore, is_status_noise

        kind = str(fields.get("kind") or "observation")
        if is_status_noise(title, kind):
            return (
                "Rejected: that looks like run/objective status, not an engagement "
                "finding. Use update_objective_status for objectives; record_finding "
                "only for discoveries about subjects (any asset class) with evidence."
            )
        row = FindingStore().record(
            self._job.project,
            {
                "title": title,
                "severity": str(fields.get("severity") or "info"),
                "kind": kind,
                "evidence": str(fields.get("evidence") or ""),
                "host": str(fields.get("host") or ""),
                "description": str(fields.get("description") or ""),
                "asset_type": str(fields.get("asset_type") or ""),
                "evidence_path": str(fields.get("evidence_path") or ""),
                "remediation": str(fields.get("remediation") or ""),
                "service": str(fields.get("service") or ""),
                "port": fields.get("port"),
                "metadata": fields.get("metadata")
                if isinstance(fields.get("metadata"), dict)
                else {},
            },
            job=self._job,
            objective=self._job.objective,
        )
        if row is None:
            return "Finding not recorded (deduped, invalid, or status noise)."
        return f"Recorded FIND-{row.seq}: {row.title[:80]}"

    def record_findings(self, findings_json: str) -> str:
        try:
            rows = json.loads(findings_json or "[]")
        except json.JSONDecodeError as exc:
            return f"Invalid JSON: {exc}"
        if not isinstance(rows, list):
            return "Expected a JSON list."
        n = 0
        for row in rows:
            if isinstance(row, dict) and row.get("title"):
                msg = self.record_finding(**row)
                if msg.startswith("Recorded"):
                    n += 1
        return f"Recorded {n} finding(s)."

    def list_findings(self, kind: str = "") -> str:
        if not self._job.project_id:
            return "No project on this job."
        qs = Finding.objects.filter(project_id=self._job.project_id).order_by("-created_at")
        if kind.strip():
            qs = qs.filter(kind=kind.strip())
        rows = list(qs[:40])
        if not rows:
            return "No findings."
        return "\n".join(
            f"FIND-{f.seq} [{f.severity}/{f.kind}] {f.title}" for f in rows
        )

    def drain_operator_guidance(self) -> list[str]:
        """Consume pending JobDirective rows into agent-facing guidance strings."""
        from django.db import transaction

        with transaction.atomic():
            pending = list(
                JobDirective.objects.select_for_update()
                .filter(job_id=self._job.id, consumed_at__isnull=True)
                .order_by("created_at")
            )
            if not pending:
                return []
            now = dj_tz.now()
            out: list[str] = []
            for d in pending:
                d.consumed_at = now
                text = (d.content or "").strip()
                if not text:
                    continue
                if d.kind == JobDirectiveKind.FOLLOWUP:
                    out.append(
                        "OPERATOR FOLLOW-UP:\n"
                        f"{text}\n\n"
                        "Honor the operator instruction under Rules of Engagement. "
                        "If they supply an exact command, prefer executing that "
                        "command (re-scan / re-run when requested)."
                    )
                else:
                    out.append(
                        "OPERATOR INSTRUCTION — revise your approach and continue "
                        f"under Rules of Engagement:\n{text}"
                    )
            JobDirective.objects.bulk_update(pending, ["consumed_at"])
        return out
