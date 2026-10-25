"""Shared HTTP helpers for peon views (JSON Accept, body parse, dual response)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterator
from functools import wraps
from typing import Any, TypeVar

from django.contrib import messages
from django.db import models
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect

from orchestrator.utils.strings import as_str_list

_M = TypeVar("_M", bound=models.Model)
_V = TypeVar("_V", bound=Callable[..., Any])


def scoped_get_or_404(model: type[_M], project, **lookup: Any) -> _M:
    """``get_object_or_404`` against the project's SQLite file (not platform default)."""
    from peon.projects.db import qs_using

    lookup = dict(lookup)
    lookup.pop("project", None)
    lookup.setdefault("project_id", getattr(project, "pk", project))
    return get_object_or_404(qs_using(model, project.pk), **lookup)


def with_project_context(view: _V) -> _V:
    """Bind the request thread to ``pk``'s project SQLite for the view body.

    Engagement models otherwise route to empty platform ``default`` when no
    thread context is set (see ``PeonDatabaseRouter``).
    """

    @wraps(view)
    def _wrapped(request: HttpRequest, pk, *args: Any, **kwargs: Any):
        from peon.projects.db import using_project
        from peon.projects.models import Project

        try:
            with using_project(pk):
                return view(request, pk, *args, **kwargs)
        except Project.DoesNotExist as exc:
            raise Http404("Project not found") from exc

    return _wrapped  # type: ignore[return-value]


def split_csv(value: Any) -> list[str]:
    """Split a comma/semicolon-separated string (or pass through a list)."""
    return as_str_list(value)


def wants_json(request: HttpRequest) -> bool:
    """True when the client asked for JSON (Accept or XHR)."""
    return (
        "application/json" in (request.headers.get("Accept") or "")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )


def json_errors(view: _V) -> _V:
    """Map common exceptions to the JSON-API error envelope used across views.

    ``KeyError`` -> 404 "not found", ``ValueError`` -> 400 ``str(exc)``,
    anything else -> 500 ``str(exc)``; always ``{"ok": False, "error": ...}``.
    This is the exact try/except block most JSON views already repeat by
    hand — use it only where that's the whole thing a view needs. A view
    that must return a different status/shape for one of these, or that
    only wraps part of its body, should keep handling that case itself.
    """

    @wraps(view)
    def _wrapped(request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            return view(request, *args, **kwargs)
        except KeyError:
            return JsonResponse({"ok": False, "error": "not found"}, status=404)
        except ValueError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:  # noqa: BLE001 — turned into the view's own JSON error
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)

    return _wrapped  # type: ignore[return-value]


def flash_json(view: _V) -> _V:
    """Let a flash-and-redirect view answer XHR callers with JSON.

    Form posts are untouched. For ``wants_json`` requests whose view returns a
    redirect, the messages it queued become ``{"ok", "error", "messages",
    "redirect"}``; ``ok`` is false when any message is an error.
    """

    @wraps(view)
    def _wrapped(request: HttpRequest, *args: Any, **kwargs: Any):
        response = view(request, *args, **kwargs)
        if not wants_json(request) or response.status_code not in (301, 302, 303):
            return response
        flashed = [
            {"level": m.level_tag or "info", "text": str(m)}
            for m in messages.get_messages(request)
        ]
        errors = [m["text"] for m in flashed if m["level"] == "error"]
        return JsonResponse(
            {
                "ok": not errors,
                "error": errors[0] if errors else "",
                "messages": flashed,
                "redirect": response.get("Location", ""),
            },
            status=400 if errors else 200,
        )

    return _wrapped  # type: ignore[return-value]


def parse_json_body(
    request: HttpRequest,
    *,
    strict: bool = False,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Parse a JSON object body.

    - ``strict=True``: raise ValueError on invalid / non-object JSON.
    - otherwise: return ``fallback`` or ``{}`` on failure / non-JSON content-type.
    """
    if not (request.content_type and "application/json" in request.content_type):
        if strict:
            raise ValueError("JSON body required")
        return dict(fallback) if fallback is not None else {}
    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError as exc:
        if strict:
            raise ValueError("invalid JSON") from exc
        return dict(fallback) if fallback is not None else {}
    if not isinstance(body, dict):
        if strict:
            raise ValueError("JSON object required")
        return dict(fallback) if fallback is not None else {}
    return body


def request_value(
    request: HttpRequest,
    key: str,
    default: str = "",
    *,
    body: dict[str, Any] | None = None,
) -> str:
    """Read one field from JSON body (if present) else POST, stripped."""
    src = body if body is not None else parse_json_body(request)
    if src and key in src and src.get(key) is not None:
        return str(src.get(key) or "").strip()
    return str(request.POST.get(key) or default).strip()


def request_values(
    request: HttpRequest,
    *keys: str,
    defaults: dict[str, str] | None = None,
) -> dict[str, str]:
    """Read several string fields from JSON-or-POST."""
    body = parse_json_body(request)
    defs = defaults or {}
    return {
        k: request_value(request, k, defs.get(k, ""), body=body) for k in keys
    }


def json_or_redirect(
    request: HttpRequest,
    *,
    ok: bool,
    redirect_to: str,
    payload: dict[str, Any] | None = None,
    error: str = "",
    flash: str = "",
    flash_level: str = "success",
    status: int = 200,
) -> HttpResponse:
    """Return JsonResponse when client wants JSON, else flash + redirect."""
    if wants_json(request):
        body = dict(payload or {})
        body.setdefault("ok", ok)
        if error and not ok:
            body.setdefault("error", error)
        return JsonResponse(body, status=status if not ok else 200)
    if flash:
        getattr(messages, flash_level, messages.info)(request, flash)
    elif error and not ok:
        messages.error(request, error)
    return redirect(redirect_to)


async def _pump(events: Iterator[str]) -> AsyncIterator[str]:
    """Drive a blocking generator from worker threads, one chunk at a time."""
    from asgiref.sync import sync_to_async

    done = object()
    step = sync_to_async(lambda: next(events, done), thread_sensitive=False)
    try:
        while True:
            chunk = await step()
            if chunk is done:
                return
            yield chunk  # type: ignore[misc]
    finally:
        try:
            await sync_to_async(events.close, thread_sensitive=False)()  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            # ValueError: a cancelled step is still running in its thread; the
            # generator finishes that tick and is collected afterwards.
            pass


def sse_response(request: HttpRequest, events: Iterator[str]) -> StreamingHttpResponse:
    """``text/event-stream`` from a blocking generator under WSGI or ASGI.

    Django buffers synchronous iterators completely when serving ASGI, which
    would hold an endless stream forever; there the generator is pumped from
    worker threads instead.
    """
    from django.core.handlers.asgi import ASGIRequest

    body = _pump(events) if isinstance(request, ASGIRequest) else events
    resp = StreamingHttpResponse(body, content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"
    return resp

