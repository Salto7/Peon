"""HTTP endpoints for sandbox PTY terminals (SSE out + POST in)."""

from __future__ import annotations

import base64
import logging

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_http_methods

from peon.projects.http_helpers import parse_json_body, sse_response, with_project_context
from peon.projects.models import Project
from peon.projects.streaming import audit_project_stream
from peon.projects.terminal_session import get_registry

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
    reg = get_registry()

    def event_stream():
        # ``retry`` keeps EventSource from hammering a restarted dev server.
        yield "retry: 2000\nevent: ready\ndata: ok\n\n"
        try:
            for chunk in reg.iter_output(sess):
                if chunk is None:
                    # Comment line: keeps proxies from timing out the idle stream
                    # and lets the server notice a client that went away.
                    yield ": ping\n\n"
                    continue
                payload = base64.b64encode(chunk).decode("ascii")
                yield f"event: out\ndata: {payload}\n\n"
        except Exception as exc:
            logger.warning("terminal stream %s failed: %s", sess.id[:8], exc)
            yield f"event: fail\ndata: {str(exc)[:200]}\n\n"
            return
        # Only reached when the shell actually ended (or was closed).
        yield "event: exit\ndata: closed\n\n"

    return sse_response(request, event_stream())


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
        if isinstance(raw, str) and body.get("encoding") == "base64":
            data = base64.b64decode(raw)
        elif isinstance(raw, str):
            data = raw.encode("utf-8", errors="replace")
        else:
            data = bytes(raw or b"")
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
