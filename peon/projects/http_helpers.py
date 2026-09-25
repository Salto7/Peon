"""Shared HTTP helpers for peon views (JSON Accept, body parse, dual response)."""

from __future__ import annotations

import json
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect


def wants_json(request: HttpRequest) -> bool:
    """True when the client asked for JSON (Accept or XHR)."""
    return (
        "application/json" in (request.headers.get("Accept") or "")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )


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


def split_csv(raw: str | None) -> list[str]:
    """Split a comma-separated string into non-empty stripped parts."""
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


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
