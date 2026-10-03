"""URL config — operator UI, catalog, admin, health."""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health(_request):
    from django.conf import settings

    proxy_on = bool(getattr(settings, "LLM_PROXY_ENABLED", False))
    proxy_url = str(getattr(settings, "LLM_PROXY_URL", "") or "").strip() or None
    return JsonResponse(
        {
            "ok": True,
            "service": "peon",
            "llm_module": str(getattr(settings, "LLM_MODULE", "litellm") or "litellm"),
            "llm_proxy_enabled": proxy_on,
            "llm_proxy_url": proxy_url if proxy_on else None,
        }
    )


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("catalog/", include("peon.projects.catalog")),
    path("learn/", include("peon.projects.learn")),
    path("", include("peon.projects.urls")),
]
