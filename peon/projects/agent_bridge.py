"""Peon bridge for orchestrator.agent (settings + JobAgentBridge)."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from django.db import transaction
from django.utils import timezone as dj_tz

from orchestrator.agent import (
    AgentBridgeBase,
    AgentRunConfig,
    agent_run_config_from_mapping,
)
from orchestrator.agent.propose import propose_agents as propose_agent_specs
from orchestrator.crew.roles.registry import RoleRegistry
from orchestrator.utils.commands import catalog_cli_in_command
from orchestrator.utils.paths import format_roe_block, safe_join
from orchestrator.utils.stream_events import (
    EVENT_READ_ARTIFACT,
    EVENT_RECORD_FINDING,
    EVENT_REPORT_NOTE,
    EVENT_RUN_CLI,
    EVENT_WRITE_ARTIFACT,
    envelope,
    event_of,
    hosts_in_command,
    is_event,
    payload_cli,
    payload_command,
    payload_hosts,
)
from peon.projects.findings import FindingStore
from peon.projects.hitl import create_operator_prompt
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
    StreamMessage,
    StreamMessageType,
)
from peon.projects.objectives import ObjectiveScheduler
from peon.projects.runtime_settings import PeonSettings
from peon.projects.streaming import emit_job_stream
from peon.projects.target_shapes import coerce_targets
from peon.projects.tasks import enqueue_job
from peon.projects.workspaces import resolve_job_workspace

logger = logging.getLogger(__name__)


def agent_run_config() -> AgentRunConfig:
    """Load agent governors from Peon Settings (DB) with .env defaults."""
    return agent_run_config_from_mapping(
        {
            "AGENT_MAX_FAILURE_REPLANS": PeonSettings.get_int("AGENT_MAX_FAILURE_REPLANS", 2),
            "AGENT_MAX_ITERATIONS": PeonSettings.get_int("AGENT_MAX_ITERATIONS", 40),
            "AGENT_MAX_SUBAGENTS": PeonSettings.get_int("AGENT_MAX_SUBAGENTS", 4),
            "AGENT_MAX_SUBAGENT_DEPTH": PeonSettings.get_int("AGENT_MAX_SUBAGENT_DEPTH", 2),
            "AGENT_RUNTIME_ENABLED": PeonSettings.get_bool("AGENT_RUNTIME_ENABLED", True),
            "CREW_REASONING_EFFORT": PeonSettings.get_str("CREW_REASONING_EFFORT", "low"),
            "CREW_REASONING_MAX_ATTEMPTS": PeonSettings.get_int("CREW_REASONING_MAX_ATTEMPTS", 1),
        }
    )


GENERATED_MARK = "<!-- peon:generated -->"


def _section_is_generated(report: str, heading: str) -> bool:
    """True when ``## heading`` exists and carries the generated-content mark."""
    lines = report.split("\n")
    want = f"## {heading}".strip().lower()
    for i, ln in enumerate(lines):
        if ln.strip().lower() != want:
            continue
        for nxt in lines[i + 1 : i + 4]:
            if nxt.strip() == GENERATED_MARK:
                return True
            if nxt.startswith("## "):
                break
    return False


def _strip_leading_heading(body: str, heading: str) -> str:
    """Drop a first line that merely repeats the section heading (any ``#`` level)."""
    lines = (body or "").strip().split("\n")
    first = lines[0].strip().lstrip("#").strip().rstrip(":").lower() if lines else ""
    if lines and lines[0].lstrip().startswith("#") and first == heading.strip().lower():
        return "\n".join(lines[1:]).strip()
    return (body or "").strip()


def _replace_section(report: str, heading: str, body: str) -> str | None:
    """Swap the body of an existing ``## heading`` section; None if absent.

    Re-writing a section (e.g. after the deterministic analyzer draft) must
    replace it, not append a second copy.
    """
    lines = report.split("\n")
    want = f"## {heading}".strip().lower()
    start = next((i for i, ln in enumerate(lines) if ln.strip().lower() == want), None)
    if start is None:
        return None
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    new = [lines[start], "", body.strip(), ""]
    return "\n".join(lines[:start] + new + lines[end:]).rstrip() + "\n"


@contextmanager
def _report_lock(lock_path: Path):
    """Exclusive advisory lock so concurrent report appends cannot clobber."""
    import fcntl

    with open(lock_path, "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class JobAgentBridge(AgentBridgeBase):
    """Django-backed bridge: stream, spawn, findings, objectives for the job agent."""

    def __init__(self, job: Job) -> None:
        self._job = job
        self.replan_requested = False
        self.idle_requested = False
        self.idle_note = ""

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
        link: str = "child",
        scope_slice: dict[str, str] | None = None,
        skill_ids: list[str] | None = None,
    ) -> str:
        kind = (link or "child").strip().lower() or "child"
        if kind not in {"peer", "child"}:
            return "Error: link must be 'peer' or 'child'"

        roles = list(role_ids or []) or list(self._job.role_ids or [])
        roles = [str(r).strip() for r in roles if str(r).strip()]
        err = self._validate_spawn_roles(roles)
        if err:
            return err

        brief = (description or "").strip()
        if scope_slice:
            slice_lines = [f"- {k}: {v}" for k, v in scope_slice.items() if str(k).strip()]
            if slice_lines:
                brief = (f"{brief}\n\n## Scope slice\n" + "\n".join(slice_lines)).strip()
                # Scope-slice values must stay inside engagement RoE for
                # technical roles (PM/analyzer do not probe).
                for role_id in roles:
                    role = RoleRegistry.shared().get(role_id)
                    if role is None or not role.is_technical:
                        continue
                    for val in scope_slice.values():
                        reason = self.assert_in_scope(str(val))
                        if reason:
                            return f"Error: scope_slice denied for {val!r}: {reason}"

        return self._spawn_linked(
            title=title,
            description=brief,
            role_ids=roles,
            link=kind,
            skill_ids=skill_ids,
        )

    def _validate_spawn_roles(self, roles: list[str]) -> str:
        """Fail closed: role must exist; deny manager class."""
        if not roles:
            return "Error: no role_ids on this job to inherit for spawn"
        reg = RoleRegistry.shared()
        for rid in roles:
            role = reg.get(rid)
            if role is None:
                return f"Error: unknown role_id {rid!r}"
            if role.is_manager:
                return f"Error: cannot spawn manager role {rid!r}"
        return ""

    def _spawn_depth(self) -> int:
        """1 = root job; +1 per parent hop (matches JobScope.depth)."""
        depth = 1
        parent = self._job.parent
        while parent is not None:
            depth += 1
            parent = parent.parent
            if depth > 64:
                break
        return depth

    def _lane_skills(self, role) -> list[str]:
        """Skills this job's objective bound it to — the lane its workers stay in."""
        from orchestrator.skills.bind import resolve_skill_ids

        raw = [
            str(s).strip() for s in (getattr(self._job, "skill_ids", None) or []) if str(s).strip()
        ]
        if not raw and self._job.objective_id and self._job.objective:
            raw = [
                str(s).strip()
                for s in (getattr(self._job.objective, "skill_ids", None) or [])
                if str(s).strip()
            ]
        return list(resolve_skill_ids(role, raw)) if raw else []

    @staticmethod
    def _canonical_skill_id(requested: str, known: list[str]) -> str:
        """Map a loosely written skill id onto a known one.

        Exact (case-insensitive) match first, then the bare name without its
        provider prefix (``name`` -> ``provider/name``) when that is
        unambiguous. Returns "" when nothing matches.
        """
        want = requested.strip().lower()
        for sid in known:
            if sid.lower() == want:
                return sid
        hits = [sid for sid in known if sid.lower().rsplit("/", 1)[-1] == want]
        return hits[0] if len(hits) == 1 else ""

    def _resolve_child_skills(
        self, roles: list[str], skill_ids: list[str] | None
    ) -> list[str] | str:
        """Bind child skills; error string on failure.

        A worker for the *same role* stays inside this job's lane (the skills
        its objective was planned with): omitting ``skill_ids`` inherits the
        lane, naming one outside it is refused — the lane's skill carries the
        workflow and recording rules the objective depends on, and silently
        swapping it is how the wrong tool ends up doing the work. Another role
        is bound by that role's own skill group.
        """
        from orchestrator.skills.bind import resolve_skill_ids

        primary = roles[0] if roles else ""
        role = RoleRegistry.shared().get(primary)
        if role is None:
            return f"Error: unknown role_id {primary!r}"
        own_role = ((self._job.role_ids or [""])[0] or "").strip()
        lane = self._lane_skills(role) if primary == own_role else []
        group = list(resolve_skill_ids(role, None))
        requested = [str(s).strip() for s in (skill_ids or []) if str(s).strip()]

        if not requested:
            return lane or list(role.skills or ()) or group

        known = list(dict.fromkeys([*lane, *group, *(role.skills or ())]))
        selected: list[str] = []
        unknown: list[str] = []
        for want in requested:
            sid = self._canonical_skill_id(want, known)
            if sid and sid not in selected:
                selected.append(sid)
            elif not sid:
                unknown.append(want)
        if unknown or not selected:
            return (
                f"Error: unknown skill_ids {', '.join(unknown or requested)} for "
                f"role {primary!r}; available: {', '.join(known) or '(none)'}"
            )
        usable = list(resolve_skill_ids(role, selected))
        if not usable:
            return (
                f"Error: no valid skill_ids for role {primary!r} "
                f"(requested: {', '.join(requested)})"
            )
        if lane:
            outside = [sid for sid in usable if sid not in lane]
            if outside:
                return (
                    f"Error: {', '.join(outside)} is outside this objective's "
                    f"skills ({', '.join(lane)}). Spawn with those skill_ids "
                    "(or leave skill_ids empty) — other skills belong to "
                    "other objectives/roles."
                )
        return usable

    def _spawn_linked(
        self,
        *,
        title: str,
        description: str,
        role_ids: list[str],
        link: str,
        skill_ids: list[str] | None = None,
    ) -> str:
        cfg = agent_run_config()
        from peon.projects.db import ensure_project_database, using_project

        if not self._job.project_id:
            return "Error: spawn requires a project on this job."
        alias = ensure_project_database(self._job.project_id)
        with using_project(self._job.project_id):
            with transaction.atomic(using=alias):
                if link == "peer":
                    if not self._job.objective_id:
                        return "Error: peer spawn requires project + objective on this job."
                    active = (
                        Job.objects.using(alias)
                        .filter(objective_id=self._job.objective_id)
                        .exclude(status__in=TERMINAL_JOB_STATUSES)
                        .count()
                    )
                    if active >= max(cfg.max_subagents + 1, 2):
                        return (
                            f"Error: too many active agents on this objective "
                            f"(max {cfg.max_subagents + 1})"
                        )
                else:
                    depth = self._spawn_depth()
                    if depth >= cfg.max_subagent_depth:
                        return f"Error: child depth limit ({cfg.max_subagent_depth})"
                    active = self._job.children.exclude(status__in=TERMINAL_JOB_STATUSES).count()
                    if active >= cfg.max_subagents:
                        return f"Error: too many active child agents (max {cfg.max_subagents})"

                roles = list(role_ids) or list(self._job.role_ids or [])
                resolved = self._resolve_child_skills(roles, skill_ids)
                if isinstance(resolved, str):
                    return resolved
                if self._job.objective_id:
                    objective = (
                        Objective.objects.using(alias)
                        .select_for_update()
                        .get(pk=self._job.objective_id)
                    )
                    if objective.status in {
                        ObjectiveStatus.COMPLETED,
                        ObjectiveStatus.CANCELLED,
                    }:
                        return (
                            "Error: cannot add work to a terminal objective; "
                            "reopen it through replan or a durable schedule first"
                        )
                child = Job.objects.using(alias).create(
                    title=(title or ("peer" if link == "peer" else "child-agent"))[:255],
                    description=description or "",
                    lifecycle=JobLifecycle.LONG,
                    status=JobStatus.PENDING,
                    role_ids=roles,
                    skill_ids=resolved,
                    project_id=self._job.project_id,
                    objective_id=self._job.objective_id,
                    parent=self._job,
                    workspace_id=self._job.workspace_id,
                    plan_text=self._job.plan_text or "",
                    plan_path=self._job.plan_path or "",
                )
        enqueue_job(str(child.id))
        self._record_handoff(child, link=link, title=title, role_ids=roles, skill_ids=resolved)
        return str(child.id)

    def _record_handoff(
        self,
        child: Job,
        *,
        link: str,
        title: str,
        role_ids: list[str],
        skill_ids: list[str] | None = None,
    ) -> None:
        from orchestrator.agent.delegation import SpawnWorkRequest
        from orchestrator.agent.transcript import record_from_event
        from orchestrator.utils.stream_events import EVENT_SPAWN_WORK, envelope

        role_id = role_ids[0] if role_ids else ""
        skills = tuple(
            str(s).strip()
            for s in (skill_ids if skill_ids is not None else (child.skill_ids or []))
            if str(s).strip()
        )
        req = SpawnWorkRequest(
            title=(title or "")[:255],
            brief="",
            role_id=role_id,
            link=link,
            skill_ids=skills,
        )
        payload = req.payload(
            job_id=str(child.id),
            parent_job_id=str(self._job.id),
        )
        payload["objective_id"] = str(self._job.objective_id or "")
        self.emit(
            "log",
            f"Delegation: spawned {link} {child.id} ({role_id or '-'})",
            metadata=envelope(EVENT_SPAWN_WORK, payload),
        )
        self.append_transcript(
            record_from_event(
                category="handoff",
                summary=f"spawned {link} job for {(title or '')[:120]}",
                project_id=str(self._job.project_id or ""),
                objective_id=str(self._job.objective_id or ""),
                job_id=str(self._job.id),
                role_id=str((self._job.role_ids or [""])[0] if self._job.role_ids else ""),
                event=EVENT_SPAWN_WORK,
                payload=payload,
            )
        )
        # Keep the governor in the picture: spawns are informs (no wake).
        if self._job.project_id:
            from peon.projects import a2a

            a2a.try_notify_governor(
                self._job.project,
                text=(
                    f"{self._role_id() or 'agent'} spawned {link} agent "
                    f"{child.id} ({role_id or '-'}): {(title or '')[:160]}"
                ),
                from_job=self._job,
            )

    def append_transcript(self, record: Any) -> None:
        """Append structured transcript to project workspace jsonl (+ optional memory)."""
        from orchestrator.agent.transcript import TranscriptRecord, append_jsonl

        if not isinstance(record, TranscriptRecord) or not self._job.project_id:
            return
        path = self._transcript_path(create=True)
        if path is None:
            return
        try:
            append_jsonl(path, record)
        except Exception:
            return
        try:
            from orchestrator.crew.memory import MemoryService

            MemoryService.remember_transcript(self._job, record)
        except Exception as exc:
            logger.warning("MemoryService.remember_transcript failed: %s", exc, exc_info=True)

    def _transcript_path(self, *, create: bool = False) -> Path | None:
        from peon.projects.workspaces import project_workspace_dir

        if not self._job.project_id:
            return None
        try:
            ws = project_workspace_dir(str(self._job.project_id), create=create)
        except Exception:
            return None
        return Path(ws) / "transcript.jsonl"

    def _transcript_digest(
        self,
        *,
        categories: tuple[str, ...] = ("error", "handoff", "gap", "replan", "outcome"),
        limit: int = 80,
    ) -> str:
        from orchestrator.agent.transcript import digest_markdown, read_jsonl

        path = self._transcript_path(create=False)
        if path is None:
            return ""
        try:
            rows = read_jsonl(path, categories=categories, limit=limit)
        except Exception:
            return ""
        oid = str(self._job.objective_id or "")
        if oid:
            scoped = [r for r in rows if not r.objective_id or r.objective_id == oid]
            rows = scoped or rows
        return digest_markdown(rows, limit=min(limit, 60))

    def list_delegations(self) -> str:
        """Spawn tree for manager oversight (children under this objective/project)."""
        from peon.projects.db import project_alias_or_default

        alias = project_alias_or_default(self._job.project_id)
        if self._job.project_id:
            qs = Job.objects.using(alias).filter(project_id=self._job.project_id)
        else:
            qs = Job.objects.using(alias).filter(pk=self._job.pk)
        if self._job.objective_id:
            qs = qs.filter(objective_id=self._job.objective_id)
        jobs = list(qs.select_related("parent").order_by("created_at")[:80])
        if not jobs:
            return "No jobs to show."

        by_id = {str(j.id): j for j in jobs}
        by_parent: dict[str, list[Job]] = {}
        for j in jobs:
            if j.parent_id and str(j.parent_id) in by_id:
                by_parent.setdefault(str(j.parent_id), []).append(j)

        roots = [j for j in jobs if not j.parent_id or str(j.parent_id) not in by_id]
        lines: list[str] = []
        seen: set[str] = set()

        def walk(job: Job, depth: int) -> None:
            jid = str(job.id)
            if jid in seen:
                return
            seen.add(jid)
            roles = ",".join(job.role_ids or []) or "-"
            lines.append(f"{'  ' * depth}- {job.id} [{job.status}] {job.title[:60]} roles={roles}")
            for child in by_parent.get(jid, []):
                walk(child, depth + 1)

        for root in roots:
            walk(root, 0)
        return "\n".join(lines) if lines else "No delegation tree."

    def wait_agents(self, *, job_ids: list[str] | None = None, timeout_seconds: int = 600) -> str:
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
            f"(call wait_for_work again later; do not block):"
        ]
        for k in pending:
            lines.append(f"- {k.title} ({k.id}) → {k.status}")
        for k in done:
            lines.append(f"- {k.title} ({k.id}) → {k.status}")
        return "\n".join(lines)

    def list_objectives(self) -> str:
        if not self._job.project_id:
            return "No project on this job."
        from peon.projects.db import qs_using

        rows = list(
            qs_using(Objective, self._job.project_id)
            .filter(project_id=self._job.project_id)
            .order_by("seq")
        )
        if not rows:
            return "No objectives."
        return "\n".join(
            f"OBJ-{o.seq} [{o.status}] {o.title} role={o.role_id or '-'}" for o in rows
        )

    def update_objective_status(self, seq: int, status: str, note: str = "") -> str:
        if not self._job.project_id:
            return "No project on this job."
        from peon.projects.db import qs_using

        obj = (
            qs_using(Objective, self._job.project_id)
            .filter(project_id=self._job.project_id, seq=int(seq))
            .first()
        )
        if obj is None:
            return f"Objective OBJ-{seq} not found."
        status = (status or "").strip().lower()
        valid = {c.value for c in ObjectiveStatus}
        if status not in valid:
            return f"Invalid status {status!r}; want one of {sorted(valid)}"

        sched = ObjectiveScheduler()
        project = self._job.project
        reason = (note or "").strip()

        # "Release to scheduler" must create a Job — status alone stalls the plan.
        if status == ObjectiveStatus.IN_PROGRESS:
            if obj.id == getattr(self._job, "objective_id", None):
                sched.mark(obj, ObjectiveStatus.IN_PROGRESS, reason=reason)
                return f"OBJ-{obj.seq} → {obj.status}"
            job = sched.create_run(
                project,
                obj,
                plan_text=self._job.plan_text or "",
                workspace_id=str(project.id),
            )
            if job is None:
                obj.refresh_from_db()
                return (
                    f"OBJ-{obj.seq} → {obj.status}"
                    + (f" ({obj.blocked_reason})" if obj.blocked_reason else "")
                    + " — no job queued"
                )
            return f"OBJ-{obj.seq} → {obj.status}; queued job {job.id}"

        if status == ObjectiveStatus.COMPLETED and not sched.all_jobs_terminal(obj):
            # The calling job is itself still active: its completion settles
            # the objective, so only *other* active jobs block it.
            others = sched.active_jobs(obj).exclude(pk=self._job.pk).count()
            if others == 0:
                return (
                    f"OBJ-{obj.seq} will complete when you finish your turn — "
                    "no other jobs are active. Give your final summary now; do "
                    "not spawn more work to close it."
                )
            return f"OBJ-{obj.seq} remains {obj.status}; {others} other tagged job(s) still active"
        sched.mark(obj, status, reason=reason)
        if status == ObjectiveStatus.BLOCKED:
            from peon.projects import a2a

            a2a.try_notify_governor(
                project,
                text=(
                    f"OBJ-{obj.seq} {obj.title} blocked: "
                    f"{reason or obj.blocked_reason or 'blocked'}"
                ),
                from_job=self._job,
                escalate=True,
            )
            try:
                from peon.projects.models import OperatorPromptKind

                create_operator_prompt(
                    project,
                    f"OBJ-{obj.seq} {obj.title}: {reason or obj.blocked_reason or 'Objective blocked'}",
                    job=self._job,
                    kind=OperatorPromptKind.BLOCKED_OBJECTIVE,
                )
            except Exception as exc:
                logger.warning("create_operator_prompt failed: %s", exc, exc_info=True)
        elif status == ObjectiveStatus.COMPLETED and obj.id != getattr(
            self._job, "objective_id", None
        ):
            # Completing a peer objective should advance the plan.
            try:
                queued = sched.enqueue_ready(project)
                if queued:
                    ids = ", ".join(str(j.id) for j in queued[:8])
                    return f"OBJ-{obj.seq} → {obj.status}; queued {len(queued)} ready job(s): {ids}"
            except Exception as exc:
                logger.warning("enqueueing ready objectives failed: %s", exc, exc_info=True)
        return f"OBJ-{obj.seq} → {obj.status}"

    def ask_operator(
        self,
        question: str,
        *,
        options: str = "",
        blocks_job: bool = True,
    ) -> str:
        """Create a typed OperatorPrompt for the global HITL popup."""
        from peon.projects.models import ObjectiveStatus, OperatorPromptKind

        text = (question or "").strip()
        if not text:
            return "Error: question is required."
        if not self._job.project_id:
            return "Error: no project on this job."
        opts = [o.strip() for o in (options or "").replace(";", ",").split(",") if o.strip()]
        try:
            prompt = create_operator_prompt(
                self._job.project,
                text,
                job=self._job,
                kind=OperatorPromptKind.AGENT_ASK,
                options=opts,
            )
        except Exception as exc:
            return f"Error: could not create operator prompt ({exc})"
        if blocks_job and self._job.objective_id and self._job.objective is not None:
            try:
                ObjectiveScheduler().mark(
                    self._job.objective,
                    ObjectiveStatus.BLOCKED,
                    reason=f"Waiting on operator: {text}"[:2000],
                )
            except Exception as exc:
                logger.warning("marking objective blocked failed: %s", exc, exc_info=True)
        return (
            f"Operator prompt {prompt.id} created (kind={prompt.kind}). "
            "Continue other work or wait_for_work / drain guidance after reply."
        )

    def _finding_payload(self, fields: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": str(fields.get("title") or "").strip(),
            "severity": str(fields.get("severity") or "info"),
            "kind": str(fields.get("kind") or "observation"),
            "evidence": str(fields.get("evidence") or ""),
            "host": str(fields.get("host") or ""),
            "description": str(fields.get("description") or fields.get("summary") or ""),
            "asset_type": str(fields.get("asset_type") or ""),
            "evidence_path": str(fields.get("evidence_path") or ""),
            "remediation": str(fields.get("remediation") or ""),
            "service": str(fields.get("service") or ""),
            "port": fields.get("port"),
            "protocol": str(fields.get("protocol") or ""),
            "cve_id": str(fields.get("cve_id") or ""),
            "cwe_id": str(fields.get("cwe_id") or ""),
            "subjects": fields.get("subjects")
            if isinstance(fields.get("subjects"), list)
            else None,
            "metadata": fields.get("metadata") if isinstance(fields.get("metadata"), dict) else {},
        }

    def record_finding(self, **fields: Any) -> str:
        if not self._job.project_id:
            return "No project on this job."
        if not str(fields.get("title") or "").strip():
            return "Error: title required."
        row = FindingStore().record(
            self._job.project,
            self._finding_payload(fields),
            job=self._job,
            objective=self._job.objective,
        )
        if row is None:
            return "Finding not recorded (deduped or invalid)."
        self.emit(
            "tool",
            f"Recorded FIND-{row.seq}: {row.title[:80]}",
            metadata=envelope(EVENT_RECORD_FINDING, {"seq": row.seq}),
        )
        return f"Recorded FIND-{row.seq}: {row.title[:80]}"

    def record_findings(self, findings_json: str) -> str:
        try:
            rows = json.loads(findings_json or "[]")
        except json.JSONDecodeError as exc:
            return f"Invalid JSON: {exc}"
        if not isinstance(rows, list):
            return "Expected a JSON list."
        if not self._job.project_id:
            return "No project on this job."
        n = 0
        store = FindingStore()
        for row in rows:
            if not isinstance(row, dict) or not str(row.get("title") or "").strip():
                continue
            if store.record(
                self._job.project,
                self._finding_payload(row),
                job=self._job,
                objective=self._job.objective,
            ):
                n += 1
        if n:
            self.emit(
                "tool",
                f"Recorded {n} finding(s).",
                metadata=envelope(EVENT_RECORD_FINDING, {"count": n}),
            )
        return f"Recorded {n} finding(s)."

    # --- Memory -------------------------------------------------------

    def _role_id(self) -> str:
        return ((self._job.role_ids or [""])[0] or "").strip()

    def recall_memory(self, query: str) -> str:
        from peon.projects import memory_store

        if not (query or "").strip():
            return "Error: query is required."
        try:
            return memory_store.recall_text(self._job.project, query, role_id=self._role_id())
        except Exception as exc:  # noqa: BLE001
            logger.warning("recall_memory failed: %s", exc, exc_info=True)
            return f"Error: memory recall failed ({exc})"

    def save_memory(
        self,
        name: str,
        kind: str,
        description: str,
        body: str = "",
        scope: str = "project",
    ) -> str:
        from peon.projects import memory_store

        scope_s = (scope or "project").strip().lower()
        if scope_s not in {"project", "platform"}:
            return "Error: scope must be 'project' or 'platform'."
        if scope_s == "project" and not self._job.project_id:
            return "Error: no project on this job."
        try:
            entry, created = memory_store.save(
                self._job.project if scope_s == "project" else None,
                name=name,
                kind=(kind or "").strip().lower(),
                description=description,
                body=body,
                role_id=self._role_id(),
                job=self._job,
                created_by=self._role_id() or "agent",
            )
        except ValueError as exc:
            return f"Error: {exc}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("save_memory failed: %s", exc, exc_info=True)
            return f"Error: could not save memory ({exc})"
        verb = "Saved" if created else "Updated"
        return f"{verb} {scope_s} memory [{entry.kind}] {entry.name}."

    def forget_memory(self, name: str, scope: str = "project") -> str:
        from peon.projects import memory_store

        scope_s = (scope or "project").strip().lower()
        project = self._job.project if scope_s == "project" else None
        if scope_s == "project" and project is None:
            return "Error: no project on this job."
        ok = memory_store.forget(project, name)
        return (
            f"Retired {scope_s} memory {memory_store.slugify(name)}."
            if ok
            else f"No active {scope_s} memory named {name!r}."
        )

    # --- Credentials (project key store) --------------------------------

    def list_credentials(self) -> str:
        from peon.projects import credentials as credential_store

        if not self._job.project_id:
            return "No project on this job."
        rows = credential_store.list_credentials(self._job.project_id)
        if not rows:
            return "No credentials stored for this project."
        lines = ["Project credentials (use get_credential(name) for the value):"]
        for row in rows:
            who = f" {row['username']}" if row["username"] else ""
            at = f"@{row['domain']}" if row["domain"] else ""
            lines.append(f"- {row['name']} [{row['kind']}]{who}{at}")
        return "\n".join(lines)

    def get_credential(self, name: str, part: str = "secret") -> str:
        from peon.projects import credentials as credential_store

        if not self._job.project_id:
            return "Error: no project on this job."
        key = (name or "").strip()
        if not key:
            return "Error: credential name is required."
        part_norm = (part or "secret").strip().lower()
        if part_norm not in {"secret", "username", "domain", "full"}:
            return "Error: part must be secret, username, domain, or full."
        found = credential_store.get_credential_secret(self._job.project_id, key)
        if found is None:
            return f"Error: no credential named {key!r} for this project."
        return found.as_env(part_norm)

    # --- A2A ----------------------------------------------------------

    def other_agents_alive(self) -> bool:
        """True if some other non-terminal agent could still message this one."""
        if not self._job.project_id:
            return False
        from peon.projects.db import qs_using
        from peon.projects.models import TERMINAL_JOB_STATUSES

        return (
            qs_using(Job, self._job.project_id)
            .filter(project_id=self._job.project_id)
            .exclude(pk=self._job.pk)
            .exclude(status__in=TERMINAL_JOB_STATUSES)
            .exists()
        )

    def send_message(
        self, to: str, content: str, *, kind: str = "inform", reply_to: str = ""
    ) -> str:
        if not self._job.project_id:
            return "Error: no project on this job."
        from peon.projects import a2a

        ids = [t.strip() for t in (to or "").split(",") if t.strip()]
        if not ids:
            return "Error: to is required (one or more agent ids, comma-separated)."
        cap = max(1, PeonSettings.get_int("A2A_MAX_RECIPIENTS", 5))
        if len(ids) > cap:
            return f"Error: {len(ids)} recipients exceeds the limit of {cap} per send."

        # Resolve every id before sending anything — a partially addressed
        # multi-send would leave the caller unsure who actually got the message.
        targets: list[Job] = []
        for raw in ids:
            target, why = a2a.resolve_recipient(self._job.project, raw, sender=self._job)
            if target is None:
                return f"Error: {raw!r} — {why}. Use list_team to see who you can message."
            if target.pk == self._job.pk:
                return "Error: that is you."
            targets.append(target)

        reply_row = None
        if reply_to.strip():
            reply_row = self._own_received_message(reply_to.strip())
            if reply_row is None:
                return (
                    f"Error: {reply_to!r} is not a message you received — "
                    "reply_to must be a message_id from your own inbox."
                )

        sent: list[str] = []
        for target in targets:
            try:
                row = a2a.send_message(
                    self._job.project,
                    to_job=target,
                    text=content,
                    from_job=self._job,
                    kind=kind if kind in {"inform", "request", "reply"} else "inform",
                    reply_to=reply_row,
                )
            except ValueError as exc:
                sent.append(f"{a2a.agent_label(target)}: Error — {exc}")
                continue
            target.refresh_from_db()
            state = (
                "woken and queued"
                if target.status == JobStatus.PENDING
                else f"delivered ({target.status})"
            )
            sent.append(f"{a2a.agent_label(target)} [{state}], message id {row.pk}")
        return (
            "Sent:\n"
            + "\n".join(f"- {line}" for line in sent)
            + "\n\nReplies arrive in your inbox (check_inbox); use message_status "
            "to check whether a specific send was read."
        )

    def _own_received_message(self, message_id: str):
        from peon.projects.db import qs_using
        from peon.projects.models import AgentMessage

        try:
            import uuid

            mid = uuid.UUID(message_id)
        except ValueError:
            return None
        return qs_using(AgentMessage, self._job.project_id).filter(pk=mid, to_job=self._job).first()

    def message_status(self, limit: int = 10) -> str:
        if not self._job.project_id:
            return "No project on this job."
        from peon.projects import a2a

        rows = a2a.sent_message_status(self._job, limit=int(limit or 10))
        if not rows:
            return "You have not sent any messages yet."
        lines = []
        for r in rows:
            flags = []
            if r["read"]:
                flags.append("read")
            else:
                flags.append("unread")
            if r["replied"]:
                flags.append("replied")
            line = f"{r['message_id']} → {r['to']} [{', '.join(flags)}]"
            if r["reply_text"]:
                line += f"\n  reply: {r['reply_text']}"
            lines.append(line)
        return "\n".join(lines)

    def check_inbox(self) -> str:
        if not self._job.project_id:
            return "No project on this job."
        from peon.projects import a2a

        parts: list[str] = []
        # Operator steering (non-replan) reaches a running agent through here.
        guidance = self.drain_operator_guidance(kinds={"steer", "followup"})
        if guidance:
            parts.append("## Operator guidance\n" + "\n\n".join(guidance))
        rows = a2a.drain_inbox(self._job)
        if rows:
            parts.append(a2a.render_messages(rows))
        return "\n\n".join(parts) if parts else "Inbox empty."

    def go_idle(self, reason: str = "") -> str:
        if not self._job.project_id:
            return "Error: no project on this job."
        if not self.other_agents_alive():
            return (
                "Not idling: no other agent is alive to message you. "
                "Finish your work and report your result instead."
            )
        self.idle_requested = True
        self.idle_note = (reason or "waiting for another agent")[:255]
        return (
            "Understood — you will be parked as IDLE when you finish this turn "
            "and woken, with your context, when another agent or the operator "
            "messages you. End your turn now with a one-paragraph summary of "
            "what you completed and exactly what you are waiting for."
        )

    def list_team(self) -> str:
        if not self._job.project_id:
            return "No project on this job."
        from peon.projects import a2a

        return a2a.team_listing(self._job.project, viewer=self._job)

    def record_assets(self, assets_json: str, relations_json: str = "") -> str:
        """Producer API for the asset graph: assets + typed relations, with provenance."""
        if not self._job.project_id:
            return "No project on this job."

        def parse(raw: str, label: str):
            text = (raw or "").strip()
            if not text:
                return [], ""
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                return None, f"Invalid {label} JSON: {exc}"
            if not isinstance(value, list):
                return None, f"{label} must be a JSON list."
            return value, ""

        assets, err = parse(assets_json, "assets_json")
        if err:
            return err
        relations, err = parse(relations_json, "relations_json")
        if err:
            return err
        if not assets and not relations:
            return "Nothing to record: pass assets_json and/or relations_json."
        from peon.projects.asset_graph import AssetGraphEngine

        role = ((self._job.role_ids or [""])[0] or "agent").strip()
        try:
            report = AssetGraphEngine(self._job.project).record(assets, relations, source=role)
        except Exception as exc:
            logger.warning("record_assets failed: %s", exc, exc_info=True)
            return f"Error: could not update the asset graph ({exc})"
        msg = (
            f"Recorded {report['assets']} asset(s), {report['relations']} relation(s); "
            f"graph now {report['nodes']} nodes / {report['edges']} edges."
        )
        if report["skipped"]:
            msg += " Skipped: " + "; ".join(report["skipped"][:6])
        return msg

    def list_findings(self, kind: str = "") -> str:
        if not self._job.project_id:
            return "No project on this job."
        from peon.projects.db import qs_using

        qs = (
            qs_using(Finding, self._job.project_id)
            .filter(project_id=self._job.project_id)
            .order_by("-created_at")
        )
        if kind.strip():
            qs = qs.filter(kind=kind.strip())
        rows = list(qs[:500])
        if not rows:
            return "No findings."
        return "\n".join(
            f"FIND-{f.seq} [{f.severity}/{f.kind}] {f.title}"
            + (f" host={f.host}" if f.host else "")
            + (f" port={f.port}" if f.port is not None else "")
            + (
                f" — {(f.evidence or f.description or '')[:120]}"
                if (f.evidence or f.description)
                else ""
            )
            for f in rows
        )

    def write_workspace_artifact(self, path: str, content: str) -> str:
        """Write a text deliverable under ``workspace/`` (no command execution).

        Lets a supervisor persist its lane's output (a synthesis, a manifest)
        without a shell, so producing a file never needs another spawned job.
        """
        rel = (path or "").strip().lstrip("/")
        parts = Path(rel).parts
        if not rel or ".." in parts or not parts or parts[0] != "workspace":
            return "Error: path must be relative and under workspace/."
        text = content if isinstance(content, str) else str(content or "")
        if len(text.encode("utf-8")) > 2_000_000:
            return "Error: content exceeds 2 MB."
        root = resolve_job_workspace(self._job).resolve()
        target = safe_join(root, rel)
        if target is None:
            return "Error: path escapes workspace."
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".{target.name}.tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(target)
        except OSError as exc:
            return f"Error writing {rel}: {exc}"
        self.emit(
            "tool",
            f"Wrote {rel}",
            metadata=envelope(EVENT_WRITE_ARTIFACT, {"path": rel}),
        )
        return f"Wrote {rel} ({len(text)} chars)."

    def list_workspace_artifacts(self) -> str:
        """List evidence files prior agents left under the job workspace."""
        from peon.projects.workspaces import format_workspace_artifact_index

        ws = resolve_job_workspace(self._job)
        text = format_workspace_artifact_index(ws)
        return text or "(no workspace artifacts yet)"

    def read_workspace_artifact(self, path: str, max_chars: int = 500_000) -> str:
        """Read one evidence file under ``workspace/`` or ``findings/`` (host FS).

        Default/ceiling are large enough for MCP pipeline JSON (often 200–500KB).
        Pass a higher ``max_chars`` (up to 5_000_000) for oversized handoffs —
        truncated JSON silently drops trailing arrays.
        """
        rel = (path or "").strip().lstrip("/")
        if not rel or ".." in Path(rel).parts:
            return "Error: provide a relative path under workspace/ or findings/."
        root = resolve_job_workspace(self._job).resolve()
        target = safe_join(root, rel)
        if target is None or not target.is_file():
            return f"Error: artifact not found: {rel}"
        try:
            target.relative_to(root)
        except ValueError:
            return "Error: path escapes workspace."
        # Only evidence trees — not arbitrary project files.
        top = target.relative_to(root).parts[0] if target.relative_to(root).parts else ""
        if top not in {"workspace", "findings"}:
            return "Error: only workspace/ and findings/ artifacts are readable."
        self.emit(
            "tool",
            f"read {rel}",
            metadata=envelope(EVENT_READ_ARTIFACT, {"path": rel}),
        )
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"Error reading {rel}: {exc}"
        try:
            requested = int(max_chars or 500_000)
        except (TypeError, ValueError):
            requested = 500_000
        limit = max(1_000, min(requested, 5_000_000))
        if len(text) > limit:
            return (
                text[:limit].rstrip() + f"\n\n…(truncated at {limit} chars of {len(text)}; "
                f"file={rel} — re-read with max_chars>={len(text)} to load fully)"
            )
        return text

    def write_report_note(self, section: str, body: str) -> str:
        """Append report markdown under findings/report.md — never a Finding row."""

        heading = (section or "note").strip()[:120] or "note"
        text = _strip_leading_heading(body, heading)
        if not text:
            return "Error: report note body required."
        ws = resolve_job_workspace(self._job)
        report_dir = ws / "findings"
        report_dir.mkdir(parents=True, exist_ok=True)
        path = report_dir / "report.md"
        block = f"\n## {heading}\n\n{text}\n"
        title = (
            self._job.project.title
            if self._job.project_id and self._job.project
            else self._job.title
        )
        try:
            # Parallel tool calls append concurrently: serialize the
            # read-modify-write or later notes overwrite earlier ones.
            with _report_lock(report_dir / ".report.lock"):
                existing = (
                    path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
                )
                if not existing.strip():
                    existing = f"# Penetration Test Report — {title}\n"
                if _section_is_generated(existing, heading):
                    return (
                        f"Section '{heading}' is generated from workspace "
                        "evidence and is read-only. Put your analysis in "
                        "another section and refer to it."
                    )
                updated = _replace_section(existing, heading, text)
                if updated is None:
                    updated = existing.rstrip() + "\n" + block
                tmp = path.with_name(f".{path.name}.tmp")
                tmp.write_text(updated, encoding="utf-8")
                tmp.replace(path)
        except OSError as exc:
            return f"Failed to write report note: {exc}"
        self.emit(
            "status",
            f"Report note appended: {heading}",
            metadata=envelope(EVENT_REPORT_NOTE, {"section": heading}),
        )
        return f"Appended report section '{heading}' to findings/report.md"

    def drain_operator_guidance(self, kinds: set[str] | None = None) -> list[str]:
        """Consume pending JobDirective rows into agent-facing guidance strings.

        ``kinds`` limits which directive kinds are consumed (mid-run inbox
        checks leave REPLAN for the next start, where it can take effect).
        """

        if kinds is None:
            self.replan_requested = False
        from peon.projects.db import ensure_project_database, using_project

        alias = ensure_project_database(self._job.project_id) if self._job.project_id else "default"
        with using_project(self._job.project_id):
            with transaction.atomic(using=alias):
                directives = (
                    JobDirective.objects.using(alias)
                    .select_for_update()
                    .filter(job_id=self._job.id, consumed_at__isnull=True)
                )
                if kinds is not None:
                    directives = directives.filter(kind__in=kinds)
                pending = list(directives.order_by("created_at"))
                if not pending:
                    return []
                now = dj_tz.now()
                out: list[str] = []
                for d in pending:
                    d.consumed_at = now
                    text = (d.content or "").strip()
                    if not text:
                        continue
                    if d.kind == JobDirectiveKind.REPLAN:
                        self.replan_requested = True
                        out.append(
                            "OPERATOR REPLAN — revise the engagement plan and "
                            f"continue under Rules of Engagement:\n{text}"
                        )
                    elif d.kind == JobDirectiveKind.FOLLOWUP:
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
                JobDirective.objects.using(alias).bulk_update(pending, ["consumed_at"])
        return out

    def list_agents(self) -> str:
        if not self._job.objective_id or not self._job.project_id:
            return "No objective on this job."
        from peon.projects.db import qs_using

        peers = (
            qs_using(Job, self._job.project_id)
            .filter(objective_id=self._job.objective_id)
            .exclude(pk=self._job.id)
            .order_by("created_at")[:40]
        )
        rows = [
            f"{j.id} [{j.status}] {j.title} roles={','.join(j.role_ids or []) or '-'}"
            for j in peers
        ]
        return "\n".join(rows) if rows else "No other jobs on this objective."

    def propose_agents(self, *, context_notes: str = "", max_agents: int = 4) -> str:
        if not self._job.project_id or not self._job.objective_id:
            return "Error: propose_agents requires project + objective."

        obj = self._job.objective
        primary = (obj.role_id or "").strip().split(",")[0].strip()
        if not primary and self._job.role_ids:
            primary = str(self._job.role_ids[0])
        from orchestrator.crew.roles.catalog import catalog_role_ids

        known = set(catalog_role_ids())
        allowed = [
            r.id
            for r in RoleRegistry.shared().list_roles()
            if not r.is_manager and (not known or r.id in known)
        ]
        notes = (context_notes or "").strip()
        if not notes:
            notes = f"Current job brief:\n{(self._job.description or '')[:2000]}"
        digest = self._transcript_digest()
        try:
            from orchestrator.skills.role_memory import role_skills_prompt_block

            learned = role_skills_prompt_block()
            if learned:
                notes = (notes + "\n\n" + learned).strip()
        except Exception as exc:
            logger.warning("role_skills_prompt_block failed: %s", exc, exc_info=True)
        specs = propose_agent_specs(
            objective_title=obj.title or "",
            objective_description=obj.description or "",
            acceptance=obj.acceptance_criteria or "",
            primary_role=primary,
            allowed_roles=allowed or ([primary] if primary else []),
            context_notes=notes,
            transcript_digest=digest,
            max_agents=max_agents,
        )

        if not specs:
            return "No peer agents proposed (solo work is enough, or LLM unavailable)."
        # Prefer child spawn for agent-driven fan-out (same objective, parent link).
        created: list[str] = []
        for spec in specs:
            msg = self.spawn_agent(
                title=spec.title,
                description=spec.description,
                role_ids=[spec.role_id] if spec.role_id else None,
                link="child",
            )
            created.append(f"- {msg} [{spec.role_id or primary}] {spec.title}")
        return "Spawned proposed work:\n" + "\n".join(created)

    def _roe(self):
        if not self._job.project_id:
            return None
        return getattr(self._job.project, "roe", None)

    def roe_summary(self) -> str:

        roe = self._roe()
        if roe is None:
            return "No Rules of Engagement on this project."
        return format_roe_block(roe)

    def roe_probe_block_reason(self) -> str:
        """Empty when in_scope is usable for networked probes; else deny reason."""
        roe = self._roe()
        if roe is None:
            return "No Rules of Engagement on this project."
        if not any((t.get("value") or "").strip() for t in coerce_targets(roe.in_scope)):
            return "in_scope is empty — promote targets before active probing"
        return ""

    def assert_in_scope(self, target: str) -> str:
        """Empty string = allowed; otherwise denial reason."""

        value = (target or "").strip()
        if not value:
            return "empty target"
        roe = self._roe()
        if roe is None:
            return "no RoE on project"
        from peon.projects.target_shapes import (
            hostname_of,
            is_passive_reference_host,
            target_authorized_by_scope,
        )

        exclusions = [
            (t.get("value") or "").strip().lower()
            for t in coerce_targets(roe.exclusions)
            if t.get("value")
        ]
        needle = value.lower()
        if target_authorized_by_scope(needle, exclusions):
            return f"{value!r} is excluded by RoE"
        scope_vals = [
            (t.get("value") or "").strip().lower()
            for t in coerce_targets(roe.in_scope)
            if t.get("value")
        ]
        seed_vals = [
            (t.get("value") or "").strip().lower()
            for t in coerce_targets(getattr(roe, "seed", None) or [])
            if t.get("value")
        ]
        host = hostname_of(value)
        if is_passive_reference_host(host):
            if host and target_authorized_by_scope(host, exclusions):
                return f"{value!r} is excluded by RoE"
            if scope_vals or seed_vals:
                return ""
            return "no engagement subject — cannot use passive reference hosts"
        if not scope_vals:
            return self.roe_probe_block_reason() or (
                "in_scope is empty — promote targets before active probing"
            )
        if target_authorized_by_scope(needle, scope_vals):
            return ""
        return f"{value!r} is not in authorized RoE scope"

    def duplicate_scan_reason(self, command: str) -> str:
        """Non-empty when this job already ran the same catalog CLI against the host."""
        cmd = (command or "").strip()
        cli = catalog_cli_in_command(cmd)
        if not cli:
            return ""
        hosts = set(hosts_in_command(cmd))
        if not hosts:
            return ""
        from peon.projects.db import project_alias_or_default

        alias = project_alias_or_default(self._job.project_id)
        prior = (
            StreamMessage.objects.using(alias)
            .filter(
                job_id=self._job.id,
                message_type=StreamMessageType.TOOL,
            )
            .order_by("-id")
            .values("metadata", "content")[:40]
        )
        for row in prior:
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if event_of(meta) and not is_event(meta, EVENT_RUN_CLI):
                continue
            prev_cmd = payload_command(meta)
            if not prev_cmd:
                continue
            prev_cli = payload_cli(meta) or catalog_cli_in_command(prev_cmd)
            if prev_cli != cli:
                continue
            prev_hosts = set(payload_hosts(meta) or hosts_in_command(prev_cmd))
            if hosts & prev_hosts:
                return (
                    f"similar {cli} already ran this job for "
                    f"{', '.join(sorted(hosts))}. Reuse the existing workspace "
                    "scan XML (-oX) — do not re-run the scan."
                )
        return ""

    def assert_command_allowed(self, command: str) -> str:
        """Block active probe commands when RoE scope is empty or target denied.

        Local inspection (``ls``/``cat``/``python3`` reading workspace files) is
        not a probe — even when filenames contain host-like tokens. Host checks
        apply only when a catalog/role CLI is invoked.
        """
        cmd = (command or "").strip()
        if not cmd:
            return "empty command"
        if not catalog_cli_in_command(cmd):
            return ""
        roe = self._roe()
        if roe is None:
            return "no RoE on project"

        scope_vals = [
            (t.get("value") or "").strip() for t in coerce_targets(roe.in_scope) if t.get("value")
        ]
        found = set(hosts_in_command(cmd))
        if not found:
            return ""
        if not scope_vals:
            return "in_scope is empty — cannot run networked probe commands"
        for hit in found:
            reason = self.assert_in_scope(hit)
            if reason:
                return reason
        return ""
