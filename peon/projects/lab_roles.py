"""Lab page: view and edit engagement roles (platform DB)."""

from __future__ import annotations

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import path
from django.views.decorators.http import require_GET, require_POST

from orchestrator.crew.roles.catalog import ROLE_KINDS
from peon.projects.role_store import (
    delete_role,
    ensure_seeded,
    roles_for_lab,
    upsert_role,
)


@require_GET
def lab_roles_page(request: HttpRequest) -> HttpResponse:
    ensure_seeded()
    return render(
        request,
        "lab/roles.html",
        {
            "nav": "lab",
            "lab_section": "roles",
            "roles": roles_for_lab(),
            "kinds": sorted(ROLE_KINDS),
        },
    )


@require_POST
def lab_roles_save(request: HttpRequest) -> HttpResponse:
    role_id = str(request.POST.get("role_id") or "").strip()
    kind = str(request.POST.get("kind") or "").strip()
    aliases = str(request.POST.get("aliases") or "")
    try:
        saved = upsert_role(role_id=role_id, kind=kind, aliases=aliases)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("lab_roles")
    except Exception as exc:
        messages.error(request, f"Could not save role: {exc}")
        return redirect("lab_roles")
    messages.success(request, f"Saved role `{saved.id}`.")
    return redirect("lab_roles")


@require_POST
def lab_roles_delete(request: HttpRequest, role_id: str) -> HttpResponse:
    try:
        delete_role(role_id)
    except (KeyError, ValueError) as exc:
        messages.error(request, str(exc))
        return redirect("lab_roles")
    except Exception as exc:
        messages.error(request, f"Could not delete role: {exc}")
        return redirect("lab_roles")
    messages.success(request, f"Deleted role `{role_id}`.")
    return redirect("lab_roles")


urlpatterns = [
    path("", lab_roles_page, name="lab_roles"),
    path("save/", lab_roles_save, name="lab_roles_save"),
    path("<str:role_id>/delete/", lab_roles_delete, name="lab_roles_delete"),
]
