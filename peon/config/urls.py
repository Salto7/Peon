"""URL config — operator UI, catalog, admin, health."""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health(_request):
    from django.conf import settings

    from peon.projects.llm_proxy import LlmProxy

    proxy = LlmProxy.shared()
    proxy_on = LlmProxy.intent_enabled()
    proxy_st = proxy.status()
    return JsonResponse(
        {
            "ok": True,
            "service": "peon",
            "llm_module": str(getattr(settings, "LLM_MODULE", "litellm") or "litellm"),
            "llm_proxy_enabled": proxy_on,
            "llm_proxy_running": bool(proxy_st.get("running")),
            "llm_proxy_url": proxy.proxy_url() if proxy_on else None,
        }
    )


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("catalog/", include("peon.projects.catalog")),
    path("learn/", include("peon.projects.learn")),
    path("", include("peon.projects.urls")),
]
