"""HTTP endpoints for sandbox PTY terminals (SSE out + POST in)."""

from __future__ import annotations

import base64
import logging

from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_http_methods

from peon.projects.http_helpers import parse_json_body
from peon.projects.jobs import anchor_job
from peon.projects.models import Project
from peon.projects.streaming import emit_job_stream, stream_meta
from peon.projects.terminal_session import get_registry

logger = logging.getLogger(__name__)


def _audit(project: Project, text: str, *, tag: str = "shell") -> None:
    job = anchor_job(project, prefer_live=False)
    if job is None:
        return
    emit_job_stream(
        job,
        "status",
        text,
        stream_meta(project, role="system", tag=tag, event="terminal"),
        swallow_errors=True,
    )


def _session_or_404(project: Project, session_id: str):
    sess = get_registry().get(session_id, str(project.id))
    if sess is None:
        return None, JsonResponse({"ok": False, "error": "Session not found"}, status=404)
    return sess, None


@require_http_methods(["POST"])
def project_terminal_open(request: HttpRequest, pk) -> JsonResponse:
    project = get_object_or_404(Project, pk=pk)
    body = parse_json_body(request)
    cols = int(body.get("cols") or request.POST.get("cols") or 80)
    rows = int(body.get("rows") or request.POST.get("rows") or 24)
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
def project_terminal_stream(request: HttpRequest, pk, session_id: str) -> HttpResponse:
    project = get_object_or_404(Project, pk=pk)
    sess, err = _session_or_404(project, session_id)
    if err is not None:
        return err
    reg = get_registry()

    def event_stream():
        yield "event: ready\ndata: ok\n\n"
        try:
            for chunk in reg.iter_output(sess):
                if not chunk:
                    continue
                payload = base64.b64encode(chunk).decode("ascii")
                yield f"event: out\ndata: {payload}\n\n"
        except GeneratorExit:
            return
        except Exception as exc:
            yield f"event: error\ndata: {str(exc)[:200]}\n\n"
        finally:
            yield "event: exit\ndata: closed\n\n"

    resp = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"
    return resp


@require_http_methods(["POST"])
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
def project_terminal_resize(request: HttpRequest, pk, session_id: str) -> JsonResponse:
    project = get_object_or_404(Project, pk=pk)
    sess, err = _session_or_404(project, session_id)
    if err is not None:
        return err
    body = parse_json_body(request)
    cols = int(body.get("cols") or request.POST.get("cols") or sess.cols)
    rows = int(body.get("rows") or request.POST.get("rows") or sess.rows)
    sess.resize(cols, rows)
    return JsonResponse({"ok": True, "cols": sess.cols, "rows": sess.rows})


@require_http_methods(["POST"])
def project_terminal_close(request: HttpRequest, pk, session_id: str) -> JsonResponse:
    project = get_object_or_404(Project, pk=pk)
    ok = get_registry().close(session_id, str(project.id))
    if ok:
        _audit(project, "Operator closed sandbox terminal")
    return JsonResponse({"ok": True, "closed": ok})
