"""HTTP endpoints for sandbox PTY terminals (SSE out + POST in)."""

from __future__ import annotations

import logging

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_http_methods

from peon.projects.http_helpers import parse_json_body, sse_response, with_project_context
from peon.projects.models import Project
from peon.projects.streaming import audit_project_stream
from peon.projects.terminal_session import decode_input_bytes, get_registry

logger = logging.getLogger(__name__)


def _audit(project: Project, text: str, *, tag: str = "shell") -> None:
    audit_project_stream(
        project,
        text,
        tag=tag,
        event="terminal",
        role="system",
        message_type="status",
        prefer_live=False,
    )


def _int(body: dict, request: HttpRequest, key: str, default: int) -> int:
    try:
        return int(body.get(key) or request.POST.get(key) or default)
    except (TypeError, ValueError):
        return default


def _session_or_404(project: Project, session_id: str):
    sess = get_registry().get(session_id, str(project.id))
    if sess is None:
        return None, JsonResponse({"ok": False, "error": "Session not found"}, status=404)
    return sess, None


@require_http_methods(["POST"])
@with_project_context
def project_terminal_open(request: HttpRequest, pk) -> JsonResponse:
    project = get_object_or_404(Project, pk=pk)
    body = parse_json_body(request)
    cols = _int(body, request, "cols", 80)
    rows = _int(body, request, "rows", 24)
    try:
        sess, reused = get_registry().open(str(project.id), cols=cols, rows=rows)
    except RuntimeError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=503)
    except Exception as exc:
        logger.exception("terminal open failed")
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)
    if not reused:
        _audit(project, f"Operator opened sandbox terminal ({sess.container})")
    return JsonResponse(
        {
            "ok": True,
            "session_id": sess.id,
            "container": sess.container,
            "cols": sess.cols,
            "rows": sess.rows,
            "reused": reused,
        }
    )


@require_GET
@with_project_context
def project_terminal_stream(request: HttpRequest, pk, session_id: str) -> HttpResponse:
    project = get_object_or_404(Project, pk=pk)
    sess, err = _session_or_404(project, session_id)
    if err is not None:
        return err
    return sse_response(request, get_registry().iter_sse_events(sess))


@require_http_methods(["POST"])
@with_project_context
def project_terminal_input(request: HttpRequest, pk, session_id: str) -> JsonResponse:
    project = get_object_or_404(Project, pk=pk)
    sess, err = _session_or_404(project, session_id)
    if err is not None:
        return err
    body = parse_json_body(request)
    raw = body.get("data")
    if raw is None:
        raw = request.POST.get("data") or ""
    try:
        data = decode_input_bytes(raw, base64_encoded=body.get("encoding") == "base64")
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid input"}, status=400)
    try:
        sess.write(data)
    except OSError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=410)
    return JsonResponse({"ok": True})


@require_http_methods(["POST"])
@with_project_context
def project_terminal_resize(request: HttpRequest, pk, session_id: str) -> JsonResponse:
    project = get_object_or_404(Project, pk=pk)
    sess, err = _session_or_404(project, session_id)
    if err is not None:
        return err
    body = parse_json_body(request)
    sess.resize(_int(body, request, "cols", sess.cols), _int(body, request, "rows", sess.rows))
    return JsonResponse({"ok": True, "cols": sess.cols, "rows": sess.rows})


@require_http_methods(["POST"])
@with_project_context
def project_terminal_close(request: HttpRequest, pk, session_id: str) -> JsonResponse:
    project = get_object_or_404(Project, pk=pk)
    ok = get_registry().close(session_id, str(project.id))
    if ok:
        _audit(project, "Operator closed sandbox terminal")
    return JsonResponse({"ok": True, "closed": ok})
