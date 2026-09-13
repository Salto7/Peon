"""URL config — operator UI, catalog, admin, health."""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health(_request):
    return JsonResponse({"ok": True, "service": "peon"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("catalog/", include("peon.projects.catalog")),
    path("learn/", include("peon.projects.learn")),
    path("", include("peon.projects.views")),
]
