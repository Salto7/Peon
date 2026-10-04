"""Peon bridge for orchestrator.agent (settings + JobAgentBridge)."""

from __future__ import annotations

import json
from typing import Any

from django.utils import timezone as dj_tz

from orchestrator.agent import (
    AgentBridgeBase,
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
            "AGENT_MAX_EXECUTION_SECONDS": PeonSettings.get_int(
                "AGENT_MAX_EXECUTION_SECONDS", 1800
            ),
            "AGENT_MEMORY_ENABLED": PeonSettings.get_bool(
                "AGENT_MEMORY_ENABLED", True
            ),
            "AGENT_CHECKPOINT_ENABLED": PeonSettings.get_bool(
                "AGENT_CHECKPOINT_ENABLED", True
            ),
            "AGENT_RUNTIME_ENABLED": PeonSettings.get_bool(
                "AGENT_RUNTIME_ENABLED", True
            ),
        }
    )


class JobAgentBridge(AgentBridgeBase):
    """Django-backed bridge: stream, spawn, findings, objectives for the job agent."""

    def __init__(self, job: Job) -> None:
        self._job = job

    def emit(
        self, message_type: str, content: str, *, metadata: dict[str, Any] | None = None
    ) -> None:
        emit_job_stream(self._job, message_type, content or "", metadata or {})

    def spawn_agent(
        self,
        *,
        title: str,
        description: str,
        role_ids: list[str] | None = None,
        link: str = "peer",
    ) -> str:
        kind = (link or "peer").strip().lower()
        if kind == "child":
            return self._spawn_child(
                title=title, description=description, role_ids=list(role_ids or [])
            )
        if kind == "peer":
            return self._spawn_peer(
                title=title, description=description, role_ids=role_ids
            )
        return "Error: link must be 'peer' or 'child'"

    def _spawn_child(
        self, *, title: str, description: str, role_ids: list[str]
    ) -> str:
        cfg = agent_run_config()
        active = self._job.children.exclude(
            status__in=TERMINAL_JOB_STATUSES
        ).count()
        if active >= cfg.max_subagents:
            raise RuntimeError(f"Too many active child agents (max {cfg.max_subagents})")
        from peon.projects.tasks import enqueue_job

        roles = list(role_ids or []) or list(self._job.role_ids or [])
        child = Job.objects.create(
            title=(title or "child-agent")[:255],
            description=description or "",
            lifecycle=JobLifecycle.LONG,
            status=JobStatus.PENDING,
            role_ids=roles,
            project_id=self._job.project_id,
            objective_id=self._job.objective_id,
            parent=self._job,
            workspace_id=self._job.workspace_id,
            plan_text=self._job.plan_text or "",
            plan_path=self._job.plan_path or "",
        )
        enqueue_job(child)
        return str(child.id)

    def _spawn_peer(
        self,
        *,
        title: str,
        description: str,
        role_ids: list[str] | None = None,
    ) -> str:
        if not self._job.project_id or not self._job.objective_id:
            return "Error: peer spawn requires project + objective on this job."
        cfg = agent_run_config()
        active = (
            Job.objects.filter(objective_id=self._job.objective_id)
            .exclude(status__in=TERMINAL_JOB_STATUSES)
            .count()
        )
        if active >= max(cfg.max_subagents + 1, 2):
            return (
                f"Error: too many active agents on this objective "
                f"(max {cfg.max_subagents + 1})"
            )
        from peon.projects.objectives import ObjectiveScheduler

        roles = list(role_ids or []) or list(self._job.role_ids or [])
        role = roles[0] if roles else ""
        jobs = ObjectiveScheduler().enqueue_peer_jobs(
            self._job.project,
            self._job.objective,
            [
                {
                    "title": (title or "peer")[:255],
                    "description": description or "",
                    "role_id": role,
                }
            ],
            plan_text=self._job.plan_text or "",
            workspace_id=self._job.workspace_id or str(self._job.project_id),
            parent=self._job,
        )
        if not jobs:
            return "Error: peer job not created."
        return str(jobs[0].id)

    def wait_agents(
        self, *, job_ids: list[str] | None = None, timeout_seconds: int = 600
    ) -> str:
        """Non-blocking status of child agents — never holds the worker thread."""
        del timeout_seconds  # reserved for future release-wait
        qs = self._job.children.all()
        if job_ids:
            qs = qs.filter(id__in=job_ids)
        kids = list(qs)
        if not kids:
            return "No child agents found for this job."
        done = [k for k in kids if k.status in TERMINAL_JOB_STATUSES]
        pending = [k for k in kids if k.status not in TERMINAL_JOB_STATUSES]
        if not pending:
            lines = [f"All {len(kids)} child agent(s) finished:"]
            for k in done:
                lines.append(f"- {k.title} ({k.id}) → {k.status}")
            return "\n".join(lines)
        self._job.refresh_from_db()
        if self._job.status in {JobStatus.CANCELLED, JobStatus.PAUSED}:
            return f"Parent job {self._job.status}; stop waiting."
        lines = [
            f"{len(done)} finished, {len(pending)} still running "
            f"(call wait_for_agents again later; do not block):"
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
            f"OBJ-{o.seq} [{o.status}] {o.title} role={o.role_id or '-'}"
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

    def drain_peer_messages(self) -> list[str]:
        from peon.projects.agent_messaging import DjangoAgentMessaging

        rows = DjangoAgentMessaging().inbox(str(self._job.id), limit=20, consume=True)
        out: list[str] = []
        for m in rows:
            who = m.from_job_id[:8]
            refs = ", ".join(m.artifact_refs) if m.artifact_refs else ""
            extra = f"\nArtifact refs: {refs}" if refs else ""
            out.append(
                f"PEER MESSAGE ({m.type}) from job {who}:\n{m.body.strip()}{extra}"
            )
        return out

    def send_message(
        self,
        *,
        to_job_id: str,
        type: str,
        body: str,
        artifact_refs: list[str] | None = None,
    ) -> str:
        if not self._job.objective_id:
            return "Error: messaging requires a job linked to an objective."
        from peon.projects.agent_messaging import DjangoAgentMessaging

        try:
            msg = DjangoAgentMessaging().send(
                objective_id=str(self._job.objective_id),
                from_job_id=str(self._job.id),
                to_job_id=to_job_id or "",
                type=type if type in {"request", "inform", "handoff", "challenge"} else "inform",  # type: ignore[arg-type]
                body=body or "",
                artifact_refs=artifact_refs,
            )
        except Exception as exc:
            return f"Error: {exc}"
        return f"Sent {msg.type} message {msg.id[:8]}"

    def list_agents(self) -> str:
        if not self._job.objective_id:
            return "No objective on this job."
        from peon.projects.agent_messaging import DjangoAgentMessaging

        peers = DjangoAgentMessaging().list_peers(
            str(self._job.objective_id), exclude_job_id=str(self._job.id)
        )
        if not peers:
            return "No other jobs on this objective."
        return "\n".join(
            f"{p['job_id']} [{p['status']}] {p['title']} roles={','.join(p.get('roles') or p.get('skills') or []) or '-'}"
            for p in peers
        )

    def propose_agents(self, *, context_notes: str = "", max_agents: int = 4) -> str:
        if not self._job.project_id or not self._job.objective_id:
            return "Error: propose_agents requires project + objective."
        from orchestrator.agent.propose import propose_agents, specs_as_dicts
        from orchestrator.crew.roles.registry import RoleRegistry
        from peon.projects.objectives import ObjectiveScheduler

        obj = self._job.objective
        primary = (obj.role_id or "").strip().split(",")[0].strip()
        if not primary and self._job.role_ids:
            primary = str(self._job.role_ids[0])
        allowed = [
            r.id
            for r in RoleRegistry.shared().list_roles()
            if r.id not in {"project-manager", "analyzer"}
        ]
        notes = (context_notes or "").strip()
        if not notes:
            notes = f"Current job brief:\n{(self._job.description or '')[:2000]}"
        specs = propose_agents(
            objective_title=obj.title or "",
            objective_description=obj.description or "",
            acceptance=obj.acceptance_criteria or "",
            primary_role=primary,
            allowed_roles=allowed or ([primary] if primary else []),
            context_notes=notes,
            max_agents=max_agents,
        )
        if not specs:
            return "No peer agents proposed (solo work is enough, or LLM unavailable)."
        jobs = ObjectiveScheduler().enqueue_peer_jobs(
            self._job.project,
            obj,
            specs_as_dicts(specs),
            plan_text=self._job.plan_text or "",
            workspace_id=self._job.workspace_id or str(self._job.project_id),
            parent=self._job,
        )
        lines = [f"Spawned {len(jobs)} peer agent(s):"]
        for j, spec in zip(jobs, specs):
            lines.append(f"- {j.id} [{spec.role_id or '-'}] {spec.title}")
        return "\n".join(lines)

    def _roe(self):
        if not self._job.project_id:
            return None
        return getattr(self._job.project, "roe", None)

    def roe_summary(self) -> str:
        from peon.projects.targets import format_targets

        roe = self._roe()
        if roe is None:
            return "No Rules of Engagement on this project."
        scope = format_targets(roe.in_scope) or ["(empty)"]
        excl = format_targets(roe.exclusions) or ["(none)"]
        lines = [
            "Rules of Engagement:",
            f"- in_scope: {', '.join(scope)}",
            f"- exclusions: {', '.join(excl)}",
        ]
        note = (roe.authorization_note or "").strip()
        if note:
            lines.append(f"- authorization: {note[:500]}")
        return "\n".join(lines)

    def assert_in_scope(self, target: str) -> str:
        """Empty string = allowed; otherwise denial reason."""
        from peon.projects.targets import coerce_targets

        value = (target or "").strip()
        if not value:
            return "empty target"
        roe = self._roe()
        if roe is None:
            return "no RoE on project"
        exclusions = {
            (t.get("value") or "").strip().lower()
            for t in coerce_targets(roe.exclusions)
            if t.get("value")
        }
        needle = value.lower()
        if needle in exclusions or any(needle in e or e in needle for e in exclusions if e):
            return f"{value!r} is excluded by RoE"
        scope_vals = [
            (t.get("value") or "").strip().lower()
            for t in coerce_targets(roe.in_scope)
            if t.get("value")
        ]
        if not scope_vals:
            return "in_scope is empty — promote targets before active probing"
        if any(needle == s or needle in s or s in needle for s in scope_vals):
            return ""
        return f"{value!r} is not in authorized RoE scope"

    def assert_command_allowed(self, command: str) -> str:
        """Block active probe commands when RoE scope is empty or target denied."""
        import re

        cmd = (command or "").strip()
        if not cmd:
            return "empty command"
        roe = self._roe()
        if roe is None:
            return "no RoE on project"
        from peon.projects.targets import coerce_targets

        scope_vals = [
            (t.get("value") or "").strip()
            for t in coerce_targets(roe.in_scope)
            if t.get("value")
        ]
        # Passive targets from the command; if none, allow (local tools / listing).
        found = set(
            re.findall(
                r"(?:https?://[^\s\"']+|\b(?:\d{1,3}\.){3}\d{1,3}\b|"
                r"\b[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9-]{1,63})+\b)",
                cmd,
                flags=re.I,
            )
        )
        if not found:
            return ""
        if not scope_vals:
            return "in_scope is empty — cannot run networked probe commands"
        for hit in found:
            reason = self.assert_in_scope(hit)
            if reason:
                return reason
        return ""
