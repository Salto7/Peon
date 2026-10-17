"""Lab hub — Toolsmith and Dream."""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import include, path
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from orchestrator.crew.dreaming import DreamingService
from orchestrator.crew.memory import MemoryService
from peon.projects.http_helpers import parse_json_body


@require_GET
def lab_index(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "lab/index.html",
        {"nav": "lab", "lab_section": ""},
    )


@require_GET
def lab_dream_page(request: HttpRequest) -> HttpResponse:
    from peon.projects.runtime_settings import PeonSettings

    status = DreamingService.shared().status()
    catalog = MemoryService.lab_catalog()
    urls = {
        "status": "/lab/dream/api/status/",
        "catalog": "/lab/dream/api/catalog/",
        "recall": "/lab/dream/api/recall/",
        "improve": "/lab/dream/api/improve/",
        "run_cycle": "/lab/dream/api/run/",
        "clear": "/lab/dream/api/clear/",
        "improvements": "/lab/dream/api/improvements/",
    }
    interval_s = max(60, int(PeonSettings.get_int("PEON_DREAMING_INTERVAL_S", 900)))
    return render(
        request,
        "lab/dream.html",
        {
            "nav": "lab",
            "lab_section": "dream",
            "dream": status,
            "dream_interval_s": interval_s,
            "catalog": catalog,
            "catalog_data": (
                {
                    "datasets": catalog.get("datasets") or [],
                    "sessions": catalog.get("sessions") or [],
                }
            ),
            "dream_urls": urls,
        },
    )


@require_GET
def lab_dream_status(_request: HttpRequest) -> JsonResponse:
    return JsonResponse({"ok": True, **DreamingService.shared().status()})


@require_GET
def lab_dream_catalog(_request: HttpRequest) -> JsonResponse:
    catalog = MemoryService.lab_catalog()
    return JsonResponse({"ok": bool(catalog.get("ok")), **catalog})


@require_http_methods(["POST"])
def lab_dream_recall(request: HttpRequest) -> JsonResponse:
    body = parse_json_body(request, strict=False) or {}
    query = str(body.get("query") or "").strip()
    dataset = str(body.get("dataset") or "peon").strip() or "peon"
    session_id = str(body.get("session_id") or "").strip()
    if not query:
        return JsonResponse({"ok": False, "error": "query required"}, status=400)
    text = MemoryService.recall(
        query,
        datasets=[dataset],
        session_id=session_id,
        top_k=10,
    )
    return JsonResponse({"ok": True, "text": text, "dataset": dataset, "session_id": session_id})


@require_POST
def lab_dream_improve(request: HttpRequest) -> JsonResponse:
    body = parse_json_body(request, strict=False) or {}
    dataset = str(body.get("dataset") or "").strip()
    all_datasets = bool(body.get("all_datasets")) or dataset in {
        "*",
        "__all__",
        "all",
    }
    raw_ids = body.get("session_ids") or []
    if isinstance(raw_ids, str):
        session_ids = [s.strip() for s in raw_ids.split(",") if s.strip()]
    else:
        session_ids = [str(s).strip() for s in raw_ids if str(s).strip()]
    # Single-select convenience
    one = str(body.get("session_id") or "").strip()
    if one and one not in session_ids:
        session_ids.append(one)
    result = MemoryService.improve_sessions(
        dataset="" if all_datasets else (dataset or "peon"),
        all_datasets=all_datasets,
        session_ids=None if all_datasets else (session_ids or None),
        run_in_background=True,
    )
    return JsonResponse({"ok": bool(result.get("ok")), **result})


@require_POST
def lab_dream_improvements(_request: HttpRequest) -> JsonResponse:
    """Summarize suggested improvements from Cognee. Does not change memory."""
    result = DreamingService.shared().improvement_summary()
    return JsonResponse(result, status=200 if result.get("ok") else 502)


@require_POST
def lab_dream_run(_request: HttpRequest) -> JsonResponse:
    result = DreamingService.shared().run_cycle()
    return JsonResponse(result)


@require_POST
def lab_dream_clear(request: HttpRequest) -> JsonResponse:
    """Delete everything in Cognee; optionally re-index platform memory."""
    body = parse_json_body(request, strict=False) or {}
    reindex = bool(body.get("reindex", True))
    result = DreamingService.shared().clear(reindex=reindex)
    return JsonResponse(result, status=200 if result.get("ok") else 502)


urlpatterns = [
    path("", lab_index, name="lab"),
    path("toolsmith/", include("peon.projects.learn")),
    path("roles/", include("peon.projects.lab_roles")),
    path("dream/", lab_dream_page, name="lab_dream"),
    path("dream/api/status/", lab_dream_status, name="lab_dream_status"),
    path("dream/api/catalog/", lab_dream_catalog, name="lab_dream_catalog"),
    path("dream/api/recall/", lab_dream_recall, name="lab_dream_recall"),
    path("dream/api/improve/", lab_dream_improve, name="lab_dream_improve"),
    path("dream/api/run/", lab_dream_run, name="lab_dream_run"),
    path(
        "dream/api/improvements/",
        lab_dream_improvements,
        name="lab_dream_improvements",
    ),
    path("dream/api/clear/", lab_dream_clear, name="lab_dream_clear"),
]
