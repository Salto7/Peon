"""URL config — operator UI, catalog, admin, health."""

from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health(request):
    """Liveness for the container healthcheck; ``?full=1`` adds dependency status.

    The plain probe runs every few seconds, so it must not shell out to Docker
    or call Cognee.
    """
    body = {"ok": True, "service": "peon"}
    if request.GET.get("full"):
        from orchestrator.crew.memory import MemoryService
        from peon.projects.llm_proxy import LlmProxy

        proxy = LlmProxy.shared()
        proxy_on = LlmProxy.intent_enabled()
        try:
            cognee = MemoryService.health()
        except Exception as exc:
            cognee = {"ok": False, "error": str(exc)[:200]}
        body.update(
            llm_module=settings.LLM_MODULE,
            llm_proxy_enabled=proxy_on,
            llm_proxy_running=bool(proxy.status().get("running")),
            llm_proxy_url=proxy.proxy_url() if proxy_on else None,
            cognee_enabled=settings.COGNEE_ENABLED,
            cognee_ok=bool(cognee.get("ok")),
            cognee_url=cognee.get("base_url") or f"http://cognee:{settings.COGNEE_PORT}",
        )
    return JsonResponse(body)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("catalog/", include("peon.projects.catalog")),
    path("lab/", include("peon.projects.lab")),
    path("", include("peon.projects.urls")),
]
