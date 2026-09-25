"""Console chat — project chatbot (answer) or engagement control (replan / stop).

Default mode ``chat``:
  - **answer** — Q&A from project context (status, findings, objectives, RoE)
  - **work** — replan from the operator prompt (new/changed engagement work)
  - **stop** — pause the project

Explicit UI modes (instruct / replan / stop / reply) remain available as overrides.
``instruct`` is only for steering agents that are already live — never the default
path for “do this work” messages (that is always replan).
"""

from __future__ import annotations

import logging

from django.core.exceptions import ObjectDoesNotExist

from peon.projects.jobs import anchor_job, has_live_agents
from peon.projects.lifecycle import ProjectLifecycle
from orchestrator.utils.llm import LLM_NOT_CONFIGURED, llm_configured
from peon.projects.models import (
    TERMINAL_JOB_STATUSES,
    Finding,
    Job,
    JobDirectiveKind,
    JobStatus,
    Objective,
    ObjectiveStatus,
    OperatorPrompt,
    Project,
    ProjectStatus,
)
from peon.projects.streaming import emit_job_stream, stream_meta
from peon.projects.http_helpers import (
    json_or_redirect,
    request_values,
    wants_json as request_wants_json,
)

logger = logging.getLogger(__name__)

_ROUTE_SYSTEM = """Classify one operator message for Peon's project console.
Return ONLY compact JSON: {"intent":"answer"|"work"|"stop","reason":"short"}

intents:
- answer — wants information about THIS project (status, findings, objectives,
  scope/RoE, what agents did). No new engagement work.
- work — wants the engagement to do or change work. Their message becomes the
  replan brief (new checks, follow-ups, different goals, “do X and tell me…”).
- stop — pause / halt the engagement.

If both a question and a work request appear, prefer work.
If unsure between answer and work, prefer answer only when no action is requested."""

_ASK_SYSTEM = """You are Peon's project console assistant.
Answer using only the provided project context. Be concise and concrete.
Do not invent findings, hosts, or scan results. If the context lacks the answer, say so.
Do not start scans or suggest out-of-scope probing; for new work the operator should
send a work request (the console will replan)."""



def _ack(job: Job | None, project: Project, text: str, *, tag: str, event: str) -> None:
    if job is None or not (text or "").strip():
        return
    emit_job_stream(
        job,
        "status" if tag in {"need", "stop"} else "log",
        text,
        stream_meta(project, role="assistant", tag=tag, event=event),
    )


def _record_user(project: Project, text: str, *, event: str = "console_user") -> Job | None:
    job = anchor_job(project)
    if job is None or not (text or "").strip():
        return None
    emit_job_stream(
        job,
        "log",
        text,
        stream_meta(project, role="user", tag="you", event=event),
    )
    return job


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



def _classify(project: Project, text: str) -> str:
    """Return answer|work|stop (chat mode). Maps to answer / replan / stop."""
    if not llm_configured():
        # No LLM: treat trailing ? as answer; otherwise work → replan.
        return "answer" if text.strip().endswith("?") else "work"
    from orchestrator.utils.llm import chat_json

    try:
        data = chat_json(
            _ROUTE_SYSTEM,
            f"Project context:\n{_project_brief(project)}\n\n"
            f"Live agents: {'yes' if has_live_agents(project) else 'no'}\n\n"
            f"Operator message:\n{text}",
        )
    except Exception:
        logger.exception("console chat route failed")
        return "answer" if text.strip().endswith("?") else "work"
    intent = str((data or {}).get("intent") or "answer").strip().lower()
    if intent in {"replan", "instruct"}:
        # Legacy model outputs — both mean engagement work in chat mode.
        intent = "work"
    if intent not in {"answer", "work", "stop"}:
        intent = "answer"
    return intent


def _do_replan(project: Project, text: str) -> dict:
    if not llm_configured():
        raise RuntimeError(LLM_NOT_CONFIGURED)
    # Operator-named hosts/CIDRs in the work request are authorized into RoE
    # before planning (planner never expands scope on its own).
    from peon.projects.targets import authorize_operator_scope

    roe = getattr(project, "roe", None)
    if roe is not None:
        authorize_operator_scope(roe, text)
        try:
            project.roe.refresh_from_db()
        except Exception:
            pass
    user_job = _record_user(project, text, event="console_replan")
    result = ProjectLifecycle.replan_from_prompt(project, text)
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
        result = ProjectLifecycle.route_operator_instruction(
            project, text, job_id=job_id, record_stream=False
        )
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
        from orchestrator.utils.llm import chat_text

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


def pending_operator_prompts(project: Project) -> list[dict]:
    """Open HITL prompts + blocked objectives that need the operator."""
    out: list[dict] = []
    for p in OperatorPrompt.objects.filter(
        project=project, resolved_at__isnull=True
    ).select_related("job").order_by("created_at")[:20]:
        out.append(
            {
                "id": str(p.id),
                "kind": "prompt",
                "question": p.question,
                "job_id": str(p.job_id) if p.job_id else "",
                "job_title": (p.job.title if p.job_id else "") or "",
                "created_at": p.created_at.isoformat() if p.created_at else "",
            }
        )
    for obj in Objective.objects.filter(
        project=project, status=ObjectiveStatus.BLOCKED
    ).order_by("seq")[:20]:
        reason = (obj.blocked_reason or "").strip() or "Objective is blocked"
        out.append(
            {
                "id": f"obj-{obj.id}",
                "kind": "blocked_objective",
                "question": f"OBJ-{obj.seq} {obj.title}: {reason}",
                "job_id": "",
                "job_title": "",
                "objective_id": str(obj.id),
                "created_at": obj.updated_at.isoformat() if obj.updated_at else "",
            }
        )
    return out


def create_operator_prompt(
    project: Project,
    question: str,
    *,
    job: Job | None = None,
) -> OperatorPrompt:
    """Record a HITL ask and emit a NEED feed line for the console."""
    text = (question or "").strip()
    if not text:
        raise ValueError("question is required")
    prompt = OperatorPrompt.objects.create(
        project=project,
        job=job,
        question=text,
    )
    anchor = job or anchor_job(project)
    if anchor is not None:
        emit_job_stream(
            anchor,
            "status",
            f"Operator input needed: {text}",
            stream_meta(
                project,
                role="assistant",
                tag="need",
                event="need_input",
                prompt_id=str(prompt.id),
            ),
        )
    return prompt


def _resolve_prompt(prompt: OperatorPrompt, reply: str) -> dict:
    from django.utils import timezone as dj_tz

    text = (reply or "").strip()
    if not text:
        raise ValueError("Reply is required")
    prompt.reply = text
    prompt.resolved_at = dj_tz.now()
    prompt.save(update_fields=["reply", "resolved_at"])

    note = (
        "OPERATOR REPLY (human-in-the-loop):\n"
        f"Question: {prompt.question}\n"
        f"Answer: {text}\n\n"
        "Honor this under Rules of Engagement and continue the objective."
    )
    job = prompt.job
    steered: list[str] = []
    if job is not None and job.status not in TERMINAL_JOB_STATUSES:
        ProjectLifecycle.enqueue_job_directive(
            job, note, kind=JobDirectiveKind.STEER
        )
        steered.append(str(job.id))
    elif job is not None and job.status in TERMINAL_JOB_STATUSES:
        ProjectLifecycle.enqueue_job_directive(
            job, note, kind=JobDirectiveKind.FOLLOWUP
        )
        ProjectLifecycle.queue_job(job)
        steered.append(str(job.id))
    else:
        result = ProjectLifecycle.route_operator_instruction(
            prompt.project, note, record_stream=False
        )
        steered = list(result.get("job_ids") or [])
        job = anchor_job(prompt.project)

    if job is not None:
        emit_job_stream(
            job,
            "log",
            text,
            stream_meta(
                prompt.project,
                role="user",
                tag="you",
                event="operator_reply",
                prompt_id=str(prompt.id),
            ),
        )
    reply = f"Reply delivered to {len(steered)} job(s). They will continue under Rules of Engagement."
    return _with_reply(
        {
            "ok": True,
            "mode": "reply",
            "prompt_id": str(prompt.id),
            "job_ids": steered,
            "primary_job_id": str(job.id) if job else "",
        },
        prompt.project,
        reply,
        job=job,
        tag="steer",
        event="operator_reply_acked",
    )


def _do_stop(project: Project, text: str) -> dict:
    if project.status == ProjectStatus.CANCELLED:
        raise RuntimeError("Project is cancelled — cannot stop")
    ProjectLifecycle.pause_project(project)
    note = (text or "").strip() or "Operator stopped the engagement (project paused)."
    job = _record_user(project, note, event="operator_stop")
    reply = "Project paused — resume from the control bar when you want to continue."
    return _with_reply(
        {
            "ok": True,
            "mode": "stop",
            "project_status": ProjectStatus.PAUSED,
            "job_ids": [],
            "primary_job_id": str(job.id) if job else "",
        },
        project,
        reply,
        job=job,
        tag="ask",
        event="operator_stop_acked",
    )


def _first_pending_prompt(project: Project) -> OperatorPrompt | None:
    return (
        OperatorPrompt.objects.filter(project=project, resolved_at__isnull=True)
        .order_by("created_at")
        .first()
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
        pending = OperatorPrompt.objects.filter(
            project=project, id=prompt_id, resolved_at__isnull=True
        ).first()
        if pending is None:
            raise RuntimeError("Pending prompt not found or already answered")
        return _resolve_prompt(pending, text)

    if m == "reply":
        pending = _first_pending_prompt(project)
        if pending is None:
            raise RuntimeError("No pending operator prompt to answer")
        return _resolve_prompt(pending, text)

    if m == "chat":
        pending = _first_pending_prompt(project)
        intent = _classify(project, text)
        # Pending NEED + non-question message → treat as HITL reply.
        if pending is not None and intent == "answer" and not text.rstrip().endswith("?"):
            return _resolve_prompt(pending, text)
        if intent == "stop":
            return _do_stop(project, text)
        if intent == "work":
            return _do_replan(project, text)
        return _answer(project, text)

    if m == "replan":
        return _do_replan(project, text)

    # instruct — inject into current running objective agents (+ optional job)
    return _do_instruct(project, text, job_id=job_id)


def http_handle_console_chat(request, project: Project):
    """Parse request + dispatch + dual JSON/HTML response for the chat endpoint."""
    from django.urls import reverse

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
        from django.http import JsonResponse

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
