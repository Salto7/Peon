"""URL config — operator UI, catalog, admin, health."""

from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from peon.projects.llm_proxy import LlmProxy


def health(_request):
    proxy = LlmProxy.shared()
    proxy_on = LlmProxy.intent_enabled()
    proxy_st = proxy.status()
    cognee = {}
    try:
        from orchestrator.crew.memory import MemoryService

        cognee = MemoryService.health()
    except Exception as exc:
        cognee = {"ok": False, "error": str(exc)[:200]}
    return JsonResponse(
        {
            "ok": True,
            "service": "peon",
            "llm_module": str(getattr(settings, "LLM_MODULE", "litellm") or "litellm"),
            "llm_proxy_enabled": proxy_on,
            "llm_proxy_running": bool(proxy_st.get("running")),
            "llm_proxy_url": proxy.proxy_url() if proxy_on else None,
            "cognee_enabled": bool(getattr(settings, "COGNEE_ENABLED", True)),
            "cognee_ok": bool(cognee.get("ok")),
            "cognee_url": cognee.get("base_url")
            or f"http://cognee:{getattr(settings, 'COGNEE_PORT', 8001)}",
        }
    )


def _learn_redirect(_request):
    from django.shortcuts import redirect

    return redirect("lab_toolsmith", permanent=False)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("catalog/", include("peon.projects.catalog")),
    path("lab/", include("peon.projects.lab")),
    path("learn/", _learn_redirect, name="learn_redirect"),
    path("", include("peon.projects.urls")),
]
