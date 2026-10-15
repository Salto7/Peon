"""Console chat: Q&A, operator intents, and HITL prompts."""

from __future__ import annotations

import logging
from django.core.exceptions import ObjectDoesNotExist
from django.http import JsonResponse
from django.urls import reverse
from orchestrator.utils.llm import LLM_NOT_CONFIGURED, chat_json, chat_text, llm_configured
from peon.projects.crew_control import instruct_project, pause_project, replan_project
from peon.projects.http_helpers import json_or_redirect, request_values, wants_json as request_wants_json
from peon.projects.lifecycle import project_allows_operator
from peon.projects.roe_ops import authorize_operator_scope
from peon.projects.hitl import first_open_prompt, resolve_operator_prompt
from peon.projects.models import (
    AgentMessage,
    Finding,
    Job,
    JobStatus,
    ObjectiveStatus,
    Project,
    ProjectStatus,
    OperatorPrompt,
    StreamMessage,
)
from peon.projects.streaming import (
    anchor_job,
    audit_project_stream,
    has_live_agents,
    with_stream_reply,
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

def _record_user(project: Project, text: str, *, event: str = "console_user") -> Job | None:

    return audit_project_stream(
        project, text, tag="you", event=event, role="user", message_type="log"
    )

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
        "Got it — I'm replanning from your request"
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
    from peon.projects.db import qs_using

    objs = list(project.objectives.order_by("seq")[:40])
    findings = list(
        qs_using(Finding, project.id)
        .filter(project_id=project.id)
        .order_by("-updated_at")[:15]
    )
    jobs_live = project.jobs.filter(
        parent__isnull=True,
        status__in={
            JobStatus.RUNNING,
            JobStatus.PENDING,
            JobStatus.PAUSED,
            JobStatus.IDLE,
        },
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
        except Exception as exc:
            logger.warning("RoE refresh failed: %s", exc, exc_info=True)
    user_job = _record_user(project, text, event="console_replan")
    _remember_feedback(project, text, source="replan request")
    result = replan_project(project, text)
    # Anchor may be a newly created job after replan.
    job = anchor_job(project) or user_job
    if user_job is None and job is not None:
        _record_user(project, text, event="console_replan")
    reply = _replan_narration(project, result, text)
    return with_stream_reply(
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
    project_allows_operator(project)
    target = _agent_target(project, job_id) if job_id else None
    if target is not None and target.status in {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }:
        raise ValueError(
            f"{_agent_label(target)} has finished ({target.status}) and cannot take "
            "new instructions. Ask it about its work, or send a replan for new work."
        )
    job = (
        _record_to_agent(project, text, target, event="console_instruct_agent")
        if target is not None
        else _record_user(project, text, event="console_instruct")
    )
    _remember_feedback(project, text, source="instruction")
    try:
        result = instruct_project(project, text, job_id=job_id)
    except RuntimeError as exc:
        if "No running agents" in str(exc):
            return _do_replan(project, text)
        raise
    n = len(result.get("job_ids") or [])
    if target is not None:
        target.refresh_from_db()
        label = _agent_label(target)
        state = {
            JobStatus.PENDING: "It was idle and has been woken — it will pick this up now.",
            JobStatus.RUNNING: "It is working; it will see this at its next inbox check.",
            JobStatus.PAUSED: "It is paused; it will see this when resumed.",
            JobStatus.IDLE: "It is waiting on its dependencies and will see this when it starts.",
        }.get(target.status, "")
        reply = f"Sent to {label}. {state}".strip()
        result = {**result, "agent": {"id": str(target.id), "label": label, "status": target.status}}
        meta = {"agent_label": label, "from_job_id": str(target.id)}
    else:
        reply = (
            f"Instructed {n} live agent(s) with your note. "
            "They will adjust under Rules of Engagement — ask me for a status update anytime."
        )
        meta = None
    return with_stream_reply(
        {"ok": True, "mode": "instruct", **result},
        project,
        reply,
        job=job,
        tag="steer",
        event="console_instruct_reply",
        meta=meta,
    )

_AGENT_SYSTEM = """You are one agent on a Peon engagement, answering the operator
who is talking to YOU directly. Speak in the first person as that agent.
Answer using only the context provided about your own work: your objective,
what you ran, what you found, what you are waiting for. Be concise and concrete.
Do not invent results. If the context does not contain the answer, say what you
do know and what you would need to find out. You cannot take new actions in this
reply — if the operator wants you to do something, tell them to send it as an
instruction (Instruct) and you will pick it up."""


def _remember_feedback(project: Project, text: str, *, source: str) -> None:
    """Durable operator guidance → project memory (never blocks the chat)."""
    try:
        from peon.projects import memory_store

        memory_store.record_feedback(project, text, source=source)
    except Exception:  # noqa: BLE001
        logger.debug("feedback capture failed", exc_info=True)


def _agent_target(project: Project, job_id: str) -> Job:
    """The addressed agent. Terminal agents can still be asked about their work."""
    from peon.projects.db import qs_using

    job = (
        qs_using(Job, project.id)
        .filter(project_id=project.id, pk=str(job_id).strip())
        .select_related("objective")
        .first()
    )
    if job is None:
        raise ValueError("That agent is not on this project")
    return job


def _agent_label(job: Job) -> str:
    from orchestrator.crew.roles.registry import RoleRegistry

    rid = ((job.role_ids or [""])[0] or "").strip()
    role = RoleRegistry.shared().get(rid) if rid else None
    return (getattr(role, "label", "") or rid or job.title or "agent").strip()


def _addressing(job: Job) -> dict:
    return {
        "to_job_id": str(job.id),
        "to_title": job.title,
        "agent_label": _agent_label(job),
        "agent_role": ((job.role_ids or [""])[0] or ""),
    }


def _agent_context(project: Project, job: Job) -> str:
    """What this agent knows about its own work (for a persona answer)."""
    from orchestrator.crew.roles.registry import RoleRegistry
    from peon.projects import a2a
    from peon.projects.db import qs_using

    rid = ((job.role_ids or [""])[0] or "").strip()
    role = RoleRegistry.shared().get(rid) if rid else None
    obj = job.objective
    lines = [
        f"You are: {_agent_label(job)} (role {rid or '-'})",
        f"Your goal: {(getattr(role, 'goal', '') or '').strip() or '(none recorded)'}",
        f"Your task: {job.title}",
        f"State: {a2a.a2a_state(job.status)} [{job.status}]",
    ]
    if job.status == JobStatus.IDLE:
        waiting = job.idle_note or (
            "dependencies completing" if job.idle_reason == "dependencies" else "a message"
        )
        lines.append(f"You are idle, waiting for: {waiting}")
    if job.error:
        lines.append(f"Last error: {job.error[:500]}")
    if obj is not None:
        lines += [
            f"Objective OBJ-{obj.seq} [{obj.status}]: {obj.title}",
            f"Objective detail: {(obj.description or '').strip()[:800]}",
            f"Acceptance: {(obj.acceptance_criteria or '').strip()[:400]}",
        ]
    result = (job.result or "").strip()
    if result:
        lines.append("Your result so far:\n" + result[:2500])
    recent = list(
        qs_using(StreamMessage, project.id)
        .filter(job_id=job.id)
        .exclude(message_type="thinking")
        .order_by("-id")[:30]
    )
    if recent:
        lines.append("Your recent activity (oldest first):")
        for m in reversed(recent):
            lines.append(f"- [{m.message_type}] {(m.content or '').strip()[:220]}")
    mail = list(
        qs_using(AgentMessage, project.id)
        .filter(to_job_id=job.id)
        .order_by("-created_at")[:6]
    )
    if mail:
        lines.append("Messages you received:")
        for m in reversed(mail):
            lines.append(f"- from {m.sender}: {m.text[:300]}")
    return "\n".join(lines)


def _ask_agent(project: Project, text: str, job: Job) -> dict:
    """Conversational answer from one agent, without disturbing its work."""
    addr = _addressing(job)
    _record_to_agent(project, text, job, event="console_ask_agent")
    if not llm_configured():
        reply = (
            f"{LLM_NOT_CONFIGURED} {addr['agent_label']} is {job.status}"
            + (f" — {job.idle_note}" if job.idle_note else "")
            + "."
        )
    else:
        try:
            reply = chat_text(
                _AGENT_SYSTEM,
                f"Context about your work:\n{_agent_context(project, job)}\n\n"
                f"Operator asks you:\n{text}",
            ).strip() or "I could not produce an answer."
        except Exception as exc:
            logger.exception("console agent ask failed")
            reply = f"Could not answer right now: {exc}"
    return with_stream_reply(
        {
            "ok": True,
            "mode": "agent_answer",
            "agent": {"id": str(job.id), "label": addr["agent_label"], "status": job.status},
        },
        project,
        reply,
        job=job,
        tag="ask",
        event="console_agent_reply",
        meta={"agent_label": addr["agent_label"], "from_job_id": str(job.id)},
    )


def _record_to_agent(project: Project, text: str, job: Job, *, event: str) -> Job | None:
    """Operator line addressed to ``job`` (shows as ``You → <agent>`` in chat)."""
    return audit_project_stream(
        project,
        text,
        tag="you",
        event=event,
        role="user",
        message_type="log",
        job=job,
        meta=_addressing(job),
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
    return with_stream_reply(
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

    pause_project(project)
    project.refresh_from_db()
    note = (text or "").strip() or "Operator stopped the engagement (project paused)."
    job = _record_user(project, note, event="operator_stop")
    reply = "Project paused — resume from the control bar when you want to continue."
    return with_stream_reply(
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


def handle_console_chat(
    project: Project,
    message: str,
    *,
    mode: str = "chat",
    job_id: str | None = None,
    prompt_id: str | None = None,
) -> dict:
    """Dispatch console chat. Default ``chat`` auto-routes; overrides stay explicit."""
    m = (mode or "chat").strip().lower()
    if m in {"auto", "ask"}:
        m = "chat"
    if m not in {"chat", "instruct", "replan", "stop", "reply"}:
        m = "chat"

    text = (message or "").strip()

    if m == "stop":
        return _do_stop(project, text)

    if not text:
        raise ValueError("Message is required")

    if prompt_id:
        from peon.projects.db import qs_using

        pending = (
            qs_using(OperatorPrompt, project.id)
            .filter(project_id=project.id, id=prompt_id, resolved_at__isnull=True)
            .first()
        )
        if pending is None:
            raise RuntimeError("Pending prompt not found or already answered")
        return resolve_operator_prompt(pending, text)

    if m == "reply":
        pending = first_open_prompt(project)
        if pending is None:
            raise RuntimeError("No pending operator prompt to answer")
        return resolve_operator_prompt(pending, text)

    if m == "chat" and job_id:
        # Addressed to one agent: a direct conversation, no intent routing.
        return _ask_agent(project, text, _agent_target(project, job_id))

    if m == "chat":
        pending = first_open_prompt(project)
        intent = _classify(project, text)
        # Pending NEED + non-question message → treat as HITL reply.
        if pending is not None and intent == "answer" and not text.rstrip().endswith("?"):
            return resolve_operator_prompt(pending, text)
        if intent == "stop":
            return _do_stop(project, text)
        if intent == "instruct":
            return _do_instruct(project, text, job_id=job_id)
        if intent == "replan":
            return _do_replan(project, text)
        return _answer(project, text)

    if m == "replan":
        return _do_replan(project, text)

    # instruct — inject into current running objective agents (+ optional job)
    return _do_instruct(project, text, job_id=job_id)

def http_handle_console_chat(request, project: Project):
    """Parse request + dispatch + dual JSON/HTML response for the chat endpoint."""

    fields = request_values(
        request, "message", "mode", "job_id", "prompt_id", defaults={"mode": "chat"}
    )
    message = fields["message"]
    mode = (fields["mode"] or "chat").strip().lower()
    job_id = fields["job_id"] or None
    prompt_id = fields["prompt_id"] or None
    detail = reverse("project_detail", kwargs={"pk": project.pk})

    if mode != "stop" and not message:
        return json_or_redirect(
            request,
            ok=False,
            redirect_to=detail,
            error="Message is required.",
            status=400,
        )

    try:
        result = handle_console_chat(
            project, message, mode=mode, job_id=job_id, prompt_id=prompt_id
        )
    except RuntimeError as exc:
        return json_or_redirect(
            request, ok=False, redirect_to=detail, error=str(exc), status=409
        )
    except ValueError as exc:
        return json_or_redirect(
            request, ok=False, redirect_to=detail, error=str(exc), status=400
        )
    except Exception as exc:
        logger.exception("console chat failed")
        return json_or_redirect(
            request,
            ok=False,
            redirect_to=detail,
            error=f"Console action failed: {exc}",
            status=500,
        )

    if request_wants_json(request):

        return JsonResponse(result)

    label = result.get("mode") or mode
    if label == "stop":
        flash = "Engagement paused."
    elif label == "answer":
        flash = "Answered."
    elif label in {"reply", "instruct", "instruct_selected", "continue"}:
        flash = f"Injected into {len(result.get('job_ids') or [])} agent(s)"
    else:
        flash = (
            f"Replanned — {result.get('objectives', 0)} objectives; "
            f"next job {result.get('primary_job_id') or '(none)'}"
        )
    return json_or_redirect(
        request, ok=True, redirect_to=detail, flash=flash, flash_level="success"
    )
