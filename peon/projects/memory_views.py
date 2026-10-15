"""Operator view of agent memory: list, add, forget (JSON)."""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_http_methods

from peon.projects import memory_store
from peon.projects.http_helpers import parse_json_body, with_project_context
from peon.projects.models import MemoryEntry, Project


def _row(e: MemoryEntry) -> dict:
    return {
        "name": e.name,
        "kind": e.kind,
        "description": e.description,
        "body": e.body,
        "role_id": e.role_id,
        "created_by": e.created_by,
        "scope": "project" if e.project_id else "platform",
        "recall_count": e.recall_count,
        "updated_at": e.updated_at.isoformat() if e.updated_at else "",
    }


@require_GET
@with_project_context
def project_memory_json(request: HttpRequest, pk) -> JsonResponse:
    project = get_object_or_404(Project, pk=pk)
    key = lambda e: (e.kind, e.name)  # noqa: E731
    return JsonResponse(
        {
            "ok": True,
            "kinds": list(memory_store.KINDS),
            "project": [_row(e) for e in sorted(memory_store._scope_qs(project), key=key)],
            "platform": [_row(e) for e in sorted(memory_store._scope_qs(None), key=key)],
        }
    )


@require_http_methods(["POST"])
@with_project_context
def project_memory_write(request: HttpRequest, pk) -> JsonResponse:
    project = get_object_or_404(Project, pk=pk)
    try:
        body = parse_json_body(request, strict=True)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    action = str(body.get("action") or "save").strip().lower()
    scope = str(body.get("scope") or "project").strip().lower()
    target = project if scope == "project" else None
    if scope not in {"project", "platform"}:
        return JsonResponse({"ok": False, "error": "scope must be project or platform"}, status=400)
    if action == "forget":
        ok = memory_store.forget(target, str(body.get("name") or ""))
        if not ok:
            return JsonResponse({"ok": False, "error": "No such active memory"}, status=404)
        return JsonResponse({"ok": True})
    if action != "save":
        return JsonResponse({"ok": False, "error": "action must be save or forget"}, status=400)
    try:
        entry, created = memory_store.save(
            target,
            name=str(body.get("name") or ""),
            kind=str(body.get("kind") or "fact").strip().lower(),
            description=str(body.get("description") or ""),
            body=str(body.get("body") or ""),
            created_by="operator",
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "created": created, "entry": _row(entry)})
