"""Lab → Roles: roles are edited in the shared catalog; keep the old URL working."""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import path, reverse
from django.views.decorators.http import require_GET


@require_GET
def lab_roles_page(request: HttpRequest) -> HttpResponse:
    return redirect(reverse("catalog") + "#roles")


urlpatterns = [
    path("", lab_roles_page, name="lab_roles"),
]
