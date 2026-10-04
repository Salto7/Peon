"""Console chat — project Q&A and operator control.

Default mode ``chat``:
  - **answer** — Q&A from project context (status, findings, objectives, RoE)
  - **instruct** — chime in or steer agents already executing the current plan
  - **replan** — add or change engagement work/objectives
  - **stop** — pause the project

Explicit UI modes (instruct / replan / stop / reply) remain available as overrides.
An instruction automatically becomes a replan when no agent is live to receive it.

Thin façade: handlers live here; intents/prompts are split modules.
"""

from __future__ import annotations

import logging

from django.http import JsonResponse
from django.urls import reverse

from peon.projects.console_intents import (  # noqa: F401
    _answer,
    _classify,
    _do_instruct,
    _do_replan,
    _do_stop,
    _fallback_intent,
)
from peon.projects.console_prompts import (  # noqa: F401
    _first_pending_prompt,
    _resolve_prompt,
    create_operator_prompt,
    pending_operator_prompts,
)
from peon.projects.http_helpers import (
    json_or_redirect,
    request_values,
    wants_json as request_wants_json,
)
from peon.projects.models import OperatorPrompt, Project

logger = logging.getLogger(__name__)


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
