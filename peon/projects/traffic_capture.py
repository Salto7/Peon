"""Browser-extension authenticated-traffic capture ingest (UI-11).

The extension (``browser-extension/``) is a separate, unauthenticated-
session client — it posts a bearer ``token`` (``Project.traffic_capture_token``,
generated on demand, never derived from the project id) rather than riding on
the operator's Django session. Captured exchanges are written to one file per
batch under the project workspace and indexed as ``Evidence(kind="traffic",
source="browser-extension")`` — the same evidence pipeline UI-2/UI-3 consume,
not a parallel store.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid

from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from peon.projects.http_helpers import parse_json_body
from peon.projects.models import Evidence, Project

logger = logging.getLogger(__name__)

_MAX_BATCH_BYTES = 8_000_000
_MAX_EXCHANGES = 500


def ensure_capture_token(project: Project) -> str:
    """The project's capture token, generating one on first use."""
    if project.traffic_capture_token:
        return project.traffic_capture_token
    return regenerate_capture_token(project)


def regenerate_capture_token(project: Project) -> str:
    """New token; invalidates whatever the extension was previously configured with."""
    token = secrets.token_urlsafe(32)
    project.traffic_capture_token = token
    project.save(using="default", update_fields=["traffic_capture_token"])
    return token


def verify_capture_token(project: Project, token: str) -> bool:
    expected = (project.traffic_capture_token or "").strip()
    return bool(expected) and secrets.compare_digest(expected, (token or "").strip())


def _clean_exchange(raw: object) -> dict | None:
    """One ``{method, url, status, request, response}``-shaped exchange, or
    ``None`` if malformed. Text bodies only — the extension itself already
    drops binary bodies (image/font/audio/video) before this ever POSTs."""
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("url") or "").strip()
    if not url:
        return None
    return {
        "method": str(raw.get("method") or "GET").strip().upper()[:16],
        "url": url[:4000],
        "status": int(raw.get("status") or 0),
        "request_headers": dict(raw.get("request_headers") or {}),
        "request_body": str(raw.get("request_body") or "")[:200_000],
        "response_headers": dict(raw.get("response_headers") or {}),
        "response_body": str(raw.get("response_body") or "")[:200_000],
        "timestamp": str(raw.get("timestamp") or ""),
    }


def record_traffic_batch(project: Project, exchanges: object) -> dict:
    """Persist one captured batch. Returns ``{ok, evidence_id, count}`` or ``{ok: False, error}``."""
    from peon.projects.workspaces import project_workspace_dir

    if not isinstance(exchanges, list) or not exchanges:
        return {"ok": False, "error": "exchanges must be a non-empty array"}
    cleaned = [c for c in (_clean_exchange(e) for e in exchanges[:_MAX_EXCHANGES]) if c is not None]
    if not cleaned:
        return {"ok": False, "error": "no usable exchanges (each needs at least a url)"}

    body = json.dumps({"exchanges": cleaned}, separators=(",", ":"))
    if len(body.encode("utf-8")) > _MAX_BATCH_BYTES:
        return {"ok": False, "error": "batch too large (8 MB cap) — capture fewer requests per send"}

    batch_id = uuid.uuid4().hex[:12]
    rel_path = f"workspace/traffic/{batch_id}.json"
    root = project_workspace_dir(str(project.pk), create=True)
    target = root / "workspace" / "traffic"
    target.mkdir(parents=True, exist_ok=True)
    file_path = target / f"{batch_id}.json"
    try:
        file_path.write_text(body, encoding="utf-8")
    except OSError as exc:
        logger.warning("traffic capture write failed for %s: %s", project.pk, exc)
        return {"ok": False, "error": f"could not write capture file: {exc}"}

    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    alias = project.db_alias()
    evidence = Evidence.objects.using(alias).create(
        project=project,
        job=None,
        kind="traffic",
        source="browser-extension",
        path=rel_path,
        content_hash=content_hash,
        notes=f"{len(cleaned)} captured exchange(s) from the browser extension.",
    )
    return {"ok": True, "evidence_id": str(evidence.pk), "count": len(cleaned)}


@require_GET
def traffic_capture_token_view(request: HttpRequest, pk) -> JsonResponse:
    """Operator-session-authenticated: view (and lazily create) this project's token."""
    project = get_object_or_404(Project, pk=pk)
    return JsonResponse({"ok": True, "token": ensure_capture_token(project)})


@require_POST
def traffic_capture_token_regenerate(request: HttpRequest, pk) -> JsonResponse:
    """Operator-session-authenticated: rotate the token (invalidates any extension
    still configured with the old one — by design, same as a credential rotation)."""
    project = get_object_or_404(Project, pk=pk)
    return JsonResponse({"ok": True, "token": regenerate_capture_token(project)})


@csrf_exempt
@require_POST
def traffic_capture_ingest(request: HttpRequest, pk) -> JsonResponse:
    """Extension-authenticated (bearer token, not the Django session — this
    client has no cookie jar for Peon). ``csrf_exempt`` is safe here because
    the token check below is the real authentication; a CSRF token would
    protect nothing a stolen bearer token doesn't already defeat.
    """
    project = get_object_or_404(Project, pk=pk)
    auth = (request.headers.get("Authorization") or "").strip()
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not verify_capture_token(project, token):
        return JsonResponse({"ok": False, "error": "invalid or missing bearer token"}, status=401)
    try:
        body = parse_json_body(request, strict=True)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    result = record_traffic_batch(project, body.get("exchanges"))
    return JsonResponse(result, status=200 if result.get("ok") else 400)


urlpatterns = [
    path(
        "projects/<uuid:pk>/traffic/token/",
        traffic_capture_token_view,
        name="traffic_capture_token",
    ),
    path(
        "projects/<uuid:pk>/traffic/token/regenerate/",
        traffic_capture_token_regenerate,
        name="traffic_capture_token_regenerate",
    ),
    path(
        "projects/<uuid:pk>/traffic/ingest/",
        traffic_capture_ingest,
        name="traffic_capture_ingest",
    ),
]

__all__ = [
    "urlpatterns",
]
