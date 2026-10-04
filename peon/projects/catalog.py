"""Catalog HTTP + skill/tool card projections (SkillRegistry / ToolCatalog)."""

from __future__ import annotations

import json

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseNotFound, JsonResponse
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from orchestrator.crew.router import RoleRouter
from orchestrator.skills.registry import SkillRegistry
from orchestrator.tools.catalog import ToolCatalog
from peon.projects.catalog_cards import CatalogCardsBase, RoleCards, SkillCards, ToolCards
from peon.projects.http_helpers import split_csv

# Stable re-exports for call sites that import from peon.projects.catalog
__all__ = [
    "CatalogCardsBase",
    "RoleCards",
    "SkillCards",
    "ToolCards",
    "catalog_page",
    "api_skills",
    "api_skills_reload",
    "api_tools_reload",
    "api_skill_detail",
    "api_tools",
    "api_tool_detail",
    "api_resolve",
    "urlpatterns",
]


def _resolve_args(request: HttpRequest) -> tuple[str, str, bool, list[str], list[str]]:
    """Parse description / lifecycle / project / skills / preferred_tags from GET or POST."""
    if request.method == "POST":
        try:
            body = json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            raise ValueError("invalid JSON") from None
        if not isinstance(body, dict):
            raise ValueError("JSON object required")
        description = str(body.get("description") or body.get("brief") or "").strip()
        lifecycle = str(body.get("lifecycle") or "auto").strip() or "auto"
        project = bool(body.get("project"))
        explicit = (
            body.get("role_ids")
            or body.get("roles")
            or body.get("skills")
            or body.get("explicit")
            or []
        )
        if isinstance(explicit, str):
            explicit = split_csv(explicit)
        tags = body.get("preferred_tags") or body.get("focus_tags") or []
        if isinstance(tags, str):
            tags = split_csv(tags)
        return description, lifecycle, project, list(explicit), list(tags)

    description = (request.GET.get("description") or request.GET.get("brief") or "").strip()
    lifecycle = (request.GET.get("lifecycle") or "auto").strip() or "auto"
    project = request.GET.get("project", "").lower() in {"1", "true", "yes"}
    return (
        description,
        lifecycle,
        project,
        split_csv(
            request.GET.get("role_ids")
            or request.GET.get("roles")
            or request.GET.get("skills")
        ),
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
            "skills": SkillCards.catalog(jobable_only=not show_all),
            "tools": ToolCards.catalog(),
            "show_all": show_all,
            "skills_reload": _reload_ctx(SkillCards),
            "tools_reload": _reload_ctx(ToolCards),
            "nav": "catalog",
        },
    )


@require_GET
def api_skills(request: HttpRequest) -> JsonResponse:
    show_all = request.GET.get("all", "").lower() in {"1", "true", "yes"}
    return JsonResponse(
        {
            "skills": SkillCards.catalog(jobable_only=not show_all),
            "aliases": SkillRegistry.shared().skill_aliases(),
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
def api_skills_reload(request: HttpRequest) -> HttpResponse:
    """Force-rescan skills via ``SkillCards.reload``."""
    return _catalog_reload(request, SkillCards, preserve_all=True)


@require_POST
def api_tools_reload(request: HttpRequest) -> HttpResponse:
    """Force-rescan tools via ``ToolCards.reload``."""
    return _catalog_reload(request, ToolCards)


@require_GET
def api_skill_detail(request: HttpRequest, name: str) -> JsonResponse | HttpResponseNotFound:
    skill = SkillRegistry.shared().load_skill(name)
    if skill is None:
        return HttpResponseNotFound(
            json.dumps({"error": "not found", "name": name}),
            content_type="application/json",
        )
    return JsonResponse({"skill": skill.to_catalog_dict()})


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
            preferred_tags=preferred_tags or None,
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
    path("api/skills/", api_skills, name="catalog_api_skills"),
    path("api/skills/reload/", api_skills_reload, name="catalog_api_skills_reload"),
    path("api/skills/<str:name>/", api_skill_detail, name="catalog_api_skill"),
    path("api/tools/", api_tools, name="catalog_api_tools"),
    path("api/tools/reload/", api_tools_reload, name="catalog_api_tools_reload"),
    path("api/tools/<str:tool_id>/", api_tool_detail, name="catalog_api_tool"),
    path("api/resolve/", api_resolve, name="catalog_api_resolve"),
]
