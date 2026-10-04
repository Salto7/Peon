"""Catalog HTTP + role/tool card projections (RoleRegistry / ToolCatalog)."""

from __future__ import annotations

import json

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseNotFound, JsonResponse
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from orchestrator.crew.router import RoleRouter
from orchestrator.crew.roles.registry import RoleRegistry
from orchestrator.tools.catalog import ToolCatalog
from peon.projects.catalog_cards import CatalogCardsBase, RoleCards, ToolCards
from peon.projects.http_helpers import parse_json_body, split_csv

__all__ = [
    "CatalogCardsBase",
    "RoleCards",
    "ToolCards",
    "catalog_page",
    "api_roles",
    "api_roles_reload",
    "api_tools_reload",
    "api_role_detail",
    "api_tools",
    "api_tool_detail",
    "api_resolve",
    "urlpatterns",
]


def _resolve_args(request: HttpRequest) -> tuple[str, str, bool, list[str], list[str]]:
    """Parse description / lifecycle / project / roles / preferred_tags from GET or POST."""

    if request.method == "POST":
        body = parse_json_body(request, strict=True)
        explicit = body.get("roles") or body.get("explicit") or []
        tags = body.get("preferred_tags") or body.get("focus_tags") or []
        return (
            str(body.get("description") or body.get("brief") or "").strip(),
            str(body.get("lifecycle") or "auto").strip() or "auto",
            bool(body.get("project")),
            split_csv(explicit) if isinstance(explicit, str) else list(explicit),
            split_csv(tags) if isinstance(tags, str) else list(tags),
        )

    return (
        (request.GET.get("description") or request.GET.get("brief") or "").strip(),
        (request.GET.get("lifecycle") or "auto").strip() or "auto",
        request.GET.get("project", "").lower() in {"1", "true", "yes"},
        split_csv(request.GET.get("roles")),
        split_csv(request.GET.get("preferred_tags") or request.GET.get("focus_tags")),
    )


@require_GET
def catalog_page(request: HttpRequest) -> HttpResponse:
    show_all = request.GET.get("all", "").lower() in {"1", "true", "yes"}

    def _reload_ctx(cards: type[CatalogCardsBase]) -> dict[str, str]:
        btn = cards.reload_button()
        return {**btn, "url": reverse(btn["url_name"])}

    return render(
        request,
        "catalog/list.html",
        {
            "roles": RoleCards.catalog(jobable_only=not show_all),
            "tools": ToolCards.catalog(),
            "show_all": show_all,
            "roles_reload": _reload_ctx(RoleCards),
            "tools_reload": _reload_ctx(ToolCards),
            "nav": "catalog",
        },
    )


@require_GET
def api_roles(request: HttpRequest) -> JsonResponse:
    show_all = request.GET.get("all", "").lower() in {"1", "true", "yes"}
    return JsonResponse(
        {
            "roles": RoleCards.catalog(jobable_only=not show_all),
        }
    )


def _catalog_reload(
    request: HttpRequest,
    cards: type[CatalogCardsBase],
    *,
    preserve_all: bool = False,
) -> HttpResponse:
    """Shared Update-button handler: ``cards.reload()`` then redirect to section."""
    diff = cards.reload()
    messages.success(request, cards.reload_flash(diff))
    url = reverse("catalog")
    if preserve_all and request.POST.get("all", "").lower() in {"1", "true", "yes"}:
        url = f"{url}?all=1"
    return redirect(f"{url}#{cards.SECTION}")


@require_POST
def api_roles_reload(request: HttpRequest) -> HttpResponse:
    """Force-rescan roles via ``RoleCards.reload``."""
    return _catalog_reload(request, RoleCards, preserve_all=True)


@require_POST
def api_tools_reload(request: HttpRequest) -> HttpResponse:
    """Force-rescan tools via ``ToolCards.reload``."""
    return _catalog_reload(request, ToolCards)


@require_GET
def api_role_detail(request: HttpRequest, name: str) -> JsonResponse | HttpResponseNotFound:
    role = RoleRegistry.shared().get(name)
    if role is None:
        return HttpResponseNotFound(
            json.dumps({"error": "not found", "name": name}),
            content_type="application/json",
        )
    return JsonResponse({"role": RoleCards.from_role(role)})


@require_GET
def api_tools(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"tools": ToolCards.catalog()})


@require_GET
def api_tool_detail(request: HttpRequest, tool_id: str) -> JsonResponse | HttpResponseNotFound:
    tool = ToolCatalog.shared().by_id(tool_id)
    if tool is None:
        return HttpResponseNotFound(
            json.dumps({"error": "not found", "id": tool_id}),
            content_type="application/json",
        )
    return JsonResponse({"tool": ToolCards.from_tool(tool)})


@require_http_methods(["GET", "POST"])
def api_resolve(request: HttpRequest) -> JsonResponse:
    try:
        description, lifecycle, project, explicit, preferred_tags = _resolve_args(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    names: list[str] = []
    if description or explicit:
        names = RoleRouter.shared().resolve(
            description,
            explicit=explicit or None,
            project=project,
        )
    return JsonResponse(
        {
            "role_ids": names,
            "description": description,
            "lifecycle": lifecycle,
            "project": project,
        }
    )


urlpatterns = [
    path("", catalog_page, name="catalog"),
    path("api/roles/", api_roles, name="catalog_api_roles"),
    path("api/roles/reload/", api_roles_reload, name="catalog_api_roles_reload"),
    path("api/roles/<str:name>/", api_role_detail, name="catalog_api_role"),
    path("api/tools/", api_tools, name="catalog_api_tools"),
    path("api/tools/reload/", api_tools_reload, name="catalog_api_tools_reload"),
    path("api/tools/<str:tool_id>/", api_tool_detail, name="catalog_api_tool"),
    path("api/resolve/", api_resolve, name="catalog_api_resolve"),
]
