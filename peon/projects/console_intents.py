"""Console chat intents: classify, answer, instruct, replan, stop."""

from __future__ import annotations

import logging

from django.core.exceptions import ObjectDoesNotExist

from orchestrator.utils.llm import (
    LLM_NOT_CONFIGURED,
    chat_json,
    chat_text,
    llm_configured,
)
from peon.projects.crew_control import (
    instruct_project,
    replan_project,
    stop_project,
)
from peon.projects.lifecycle import ProjectLifecycle
from peon.projects.models import (
    Finding,
    Job,
    JobStatus,
    ObjectiveStatus,
    Project,
    ProjectStatus,
)
from peon.projects.roe_ops import authorize_operator_scope
from peon.projects.streaming import (
    anchor_job,
    audit_project_stream,
    has_live_agents,
)

logger = logging.getLogger(__name__)

_ROUTE_SYSTEM = """Classify one operator message for Peon's project console.
Return ONLY compact JSON:
{"intent":"answer"|"instruct"|"replan"|"stop","reason":"short"}

intents:
- answer — wants information about THIS project (status, findings, objectives,
  scope/RoE, what agents did). No new engagement work.
- instruct — guidance, correction, priority, constraint, or tactical suggestion
  that live agents can apply while continuing the current objective graph.
- replan — explicitly requests a replan, adds/removes objectives or targets,
  changes the engagement goal/scope, or requests work not represented by the
  current objective graph.
- stop — pause / halt the engagement.

If both a question and an action appear, prefer instruct or replan.
Use instruct for an operator chiming in on active work. Use replan only when the
objective graph must change; if no agent is live, an instruction will be promoted
to a replan by the control plane.
If unsure between answer and action, prefer answer only when no action is requested."""

_ASK_SYSTEM = """You are Peon's project console assistant.
Answer using only the provided project context. Be concise and concrete.
Do not invent findings, hosts, or scan results. If the context lacks the answer, say so.
Do not start scans or suggest out-of-scope probing; for new work the operator should
send a work request (the console will replan)."""


def _ack(job: Job | None, project: Project, text: str, *, tag: str, event: str) -> None:

    audit_project_stream(
        project,
        text,
        job=job,
        tag=tag,
        event=event,
        role="assistant",
        message_type="status" if tag in {"need", "stop"} else "log",
    )


def _record_user(project: Project, text: str, *, event: str = "console_user") -> Job | None:

    return audit_project_stream(
        project, text, tag="you", event=event, role="user", message_type="log"
    )


def _with_reply(
    result: dict,
    project: Project,
    reply: str,
    *,
    job: Job | None = None,
    tag: str = "ask",
    event: str = "console_reply",
) -> dict:
    """Attach a chat-visible assistant reply and mirror it into the stream."""
    text = (reply or "").strip()
    out = dict(result)
    out["reply"] = text
    anchor = job or anchor_job(project)
    if text and anchor is not None:
        _ack(anchor, project, text, tag=tag, event=event)
        out.setdefault("job_ids", [])
        jid = str(anchor.id)
        if jid not in out["job_ids"]:
            out["job_ids"] = list(out.get("job_ids") or []) + [jid]
        out.setdefault("primary_job_id", jid)
    return out


def _objective_lines(project: Project, *, limit: int = 12) -> list[str]:
    rows = list(
        project.objectives.exclude(status=ObjectiveStatus.CANCELLED)
        .order_by("seq")[:limit]
    )
    lines: list[str] = []
    for o in rows:
        lines.append(f"• OBJ-{o.seq} [{o.status}] {o.title}")
    return lines


def _replan_narration(project: Project, result: dict, operator_text: str) -> str:
    """Conversational ack for a replan — always shown in the chat thread."""
    n = int(result.get("objectives") or 0)
    lines = [
        f"Got it — I'm replanning from your request"
        + (f" (“{(operator_text or '').strip()[:120]}”)" if operator_text.strip() else "")
        + f". {n} objective(s) are on the board:"
    ]
    obj_lines = _objective_lines(project)
    if obj_lines:
        lines.extend(obj_lines)
    else:
        lines.append("• (objectives are still syncing — check Ops in a moment)")
    preview = (result.get("plan_preview") or "").strip()
    if preview:
        lines.append("")
        lines.append(preview[:400].rstrip())
    lines.append("")
    lines.append(
        "Agents will pick up the next ready objective. Ask me anytime for "
        "status, findings, or to change direction."
    )
    return "\n".join(lines)


def _project_brief(project: Project) -> str:
    """Compact SoT context for answer + routing (not a domain taxonomy)."""
    objs = list(project.objectives.order_by("seq")[:40])
    findings = list(
        Finding.objects.filter(project=project).order_by("-updated_at")[:15]
    )
    jobs_live = project.jobs.filter(
        parent__isnull=True,
        status__in={JobStatus.RUNNING, JobStatus.PENDING, JobStatus.PAUSED},
    ).count()
    lines = [
        f"Project: {project.title}",
        f"Status: {project.status}",
        f"Brief: {(project.summary or '').strip() or '(none)'}",
        f"Live root jobs: {jobs_live}",
        f"Findings: {len(findings)} (showing up to 15)",
        f"Objectives: {len(objs)}",
    ]
    roe = None
    try:
        roe = project.roe
    except ObjectDoesNotExist:
        roe = None
    if roe is not None:
        in_scope = getattr(roe, "in_scope", None) or []
        if isinstance(in_scope, list) and in_scope:
            vals = []
            for row in in_scope[:12]:
                if isinstance(row, dict):
                    vals.append(str(row.get("value") or row.get("target") or row))
                else:
                    vals.append(str(row))
            lines.append("In-scope: " + ", ".join(v for v in vals if v.strip()))
    for o in objs[:20]:
        extra = ""
        if o.status == ObjectiveStatus.BLOCKED and (o.blocked_reason or "").strip():
            extra = f" — blocked: {o.blocked_reason}"
        lines.append(f"- OBJ-{o.seq} [{o.status}] {o.title}{extra}")
    done = sum(1 for o in objs if o.status == ObjectiveStatus.COMPLETED)
    lines.append(f"Objectives completed: {done}/{len(objs)}")
    for f in findings:
        title = (getattr(f, "title", None) or "").strip() or "(untitled)"
        sev = getattr(f, "severity", "") or ""
        kind = getattr(f, "kind", "") or ""
        lines.append(f"- Finding [Severity {sev}/{kind}] {title}")
    return "\n".join(lines)


def _fallback_intent(project: Project, text: str) -> str:
    """Conservative routing when the classifier LLM is unavailable."""
    normalized = " ".join((text or "").strip().lower().split())
    if normalized in {"stop", "pause", "halt", "stop project", "pause project"}:
        return "stop"
    if normalized.endswith("?"):
        return "answer"
    return "instruct" if has_live_agents(project) else "replan"


def _classify(project: Project, text: str) -> str:
    """Return answer|instruct|replan|stop for default chat mode."""
    if not llm_configured():
        return _fallback_intent(project, text)

    try:
        data = chat_json(
            _ROUTE_SYSTEM,
            f"Project context:\n{_project_brief(project)}\n\n"
            f"Live agents: {'yes' if has_live_agents(project) else 'no'}\n\n"
            f"Operator message:\n{text}",
        )
    except Exception:
        logger.exception("console chat route failed")
        return _fallback_intent(project, text)
    intent = str((data or {}).get("intent") or "answer").strip().lower()
    if intent == "work":
        # Legacy classifier output means objective-changing work.
        intent = "replan"
    if intent not in {"answer", "instruct", "replan", "stop"}:
        intent = "answer"
    return intent


def _do_replan(project: Project, text: str) -> dict:
    if not llm_configured():
        raise RuntimeError(LLM_NOT_CONFIGURED)
    # Operator-named hosts/CIDRs in the work request are authorized into RoE
    # before planning (planner never expands scope on its own).

    roe = getattr(project, "roe", None)
    if roe is not None:
        authorize_operator_scope(roe, text)
        try:
            project.roe.refresh_from_db()
        except Exception:
            pass
    user_job = _record_user(project, text, event="console_replan")
    result = replan_project(project, text)
    # Anchor may be a newly created job after replan.
    job = anchor_job(project) or user_job
    if user_job is None and job is not None:
        _record_user(project, text, event="console_replan")
    reply = _replan_narration(project, result, text)
    return _with_reply(
        {"ok": True, **result},
        project,
        reply,
        job=job,
        tag="steer",
        event="console_replan_reply",
    )


def _do_instruct(
    project: Project, text: str, *, job_id: str | None = None
) -> dict:
    """Steer live agents only. If none are live, escalate to replan."""
    if not job_id and not has_live_agents(project):
        return _do_replan(project, text)
    ProjectLifecycle.project_allows_operator(project)
    job = _record_user(project, text, event="console_instruct")
    try:
        result = instruct_project(project, text, job_id=job_id)
    except RuntimeError as exc:
        if "No running agents" in str(exc):
            return _do_replan(project, text)
        raise
    n = len(result.get("job_ids") or [])
    reply = (
        f"Instructed {n} live agent(s) with your note. "
        "They will adjust under Rules of Engagement — ask me for a status update anytime."
    )
    return _with_reply(
        {"ok": True, "mode": "instruct", **result},
        project,
        reply,
        job=job,
        tag="steer",
        event="console_instruct_reply",
    )


def _answer(project: Project, text: str) -> dict:
    job = _record_user(project, text, event="console_ask")
    if not llm_configured():
        reply = f"{LLM_NOT_CONFIGURED} Project status: {project.status}."
    else:
        try:
            reply = chat_text(
                _ASK_SYSTEM,
                f"Context:\n{_project_brief(project)}\n\nQuestion:\n{text}",
            ).strip() or "I could not produce an answer."
        except Exception as exc:
            logger.exception("console ask failed")
            reply = f"Could not answer right now: {exc}"
    return _with_reply(
        {
            "ok": True,
            "mode": "answer",
            "job_ids": [str(job.id)] if job else [],
            "primary_job_id": str(job.id) if job else "",
        },
        project,
        reply,
        job=job,
        tag="ask",
        event="console_ask_reply",
    )

def _do_stop(project: Project, text: str) -> dict:
    if project.status == ProjectStatus.CANCELLED:
        raise RuntimeError("Project is cancelled — cannot stop")

    stop_project(project)
    project.refresh_from_db()
    note = (text or "").strip() or "Operator stopped the engagement (project paused)."
    job = _record_user(project, note, event="operator_stop")
    reply = "Project paused — resume from the control bar when you want to continue."
    return _with_reply(
        {
            "ok": True,
            "mode": "stop",
            "project_status": ProjectStatus.PAUSED,
            "crew_status": project.crew_status,
            "job_ids": [],
            "primary_job_id": str(job.id) if job else "",
        },
        project,
        reply,
        job=job,
        tag="ask",
        event="operator_stop_acked",
    )
