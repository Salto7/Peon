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
from peon.projects.catalog_cards import CatalogCardsBase, RoleCards, SkillCards, ToolCards
from peon.projects.catalog_edit import (
    load_skill_source,
    load_tool_source,
    save_skill_source,
    save_tool_source,
)
from orchestrator.utils.strings import as_str_list
from peon.projects.http_helpers import parse_json_body
from orchestrator.skills.registry import SkillRegistry

__all__ = [
    "CatalogCardsBase",
    "RoleCards",
    "SkillCards",
    "ToolCards",
    "catalog_page",
    "api_roles",
    "api_roles_reload",
    "api_skills",
    "api_skills_reload",
    "api_skill_detail",
    "api_tools_reload",
    "api_role_detail",
    "api_tools",
    "api_tool_detail",
    "api_resolve",
    "api_skill_source",
    "api_tool_source",
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
            as_str_list(explicit) if isinstance(explicit, str) else list(explicit),
            as_str_list(tags) if isinstance(tags, str) else list(tags),
        )

    return (
        (request.GET.get("description") or request.GET.get("brief") or "").strip(),
        (request.GET.get("lifecycle") or "auto").strip() or "auto",
        request.GET.get("project", "").lower() in {"1", "true", "yes"},
        as_str_list(request.GET.get("roles")),
        as_str_list(request.GET.get("preferred_tags") or request.GET.get("focus_tags")),
    )


@require_GET
def catalog_page(request: HttpRequest) -> HttpResponse:
    show_all = request.GET.get("all", "").lower() in {"1", "true", "yes"}

    def _reload_ctx(cards: type[CatalogCardsBase]) -> dict[str, str]:
        btn = cards.reload_button()
        return {**btn, "url": reverse(btn["url_name"])}

    import json as _json

    edit_urls = {
        "skill_source": "/catalog/api/skills/{id}/source/",
        "tool_source": "/catalog/api/tools/{id}/source/",
    }
    return render(
        request,
        "catalog/list.html",
        {
            "roles": RoleCards.catalog(jobable_only=not show_all),
            "skills": SkillCards.catalog(),
            "tools": ToolCards.catalog(),
            "show_all": show_all,
            "roles_reload": _reload_ctx(RoleCards),
            "skills_reload": _reload_ctx(SkillCards),
            "tools_reload": _reload_ctx(ToolCards),
            "catalog_edit_urls_json": _json.dumps(edit_urls),
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
def api_skills(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"skills": SkillCards.catalog()})


@require_POST
def api_skills_reload(request: HttpRequest) -> HttpResponse:
    """Force-rescan skills via ``SkillCards.reload``."""
    return _catalog_reload(request, SkillCards)


@require_GET
def api_skill_detail(request: HttpRequest, skill_id: str) -> JsonResponse | HttpResponseNotFound:
    skill = SkillRegistry.shared().get(skill_id)
    if skill is None:
        return HttpResponseNotFound(
            json.dumps({"error": "not found", "id": skill_id}),
            content_type="application/json",
        )
    return JsonResponse({"skill": SkillCards.from_skill(skill)})


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
def api_skill_source(request: HttpRequest, skill_id: str) -> JsonResponse:
    if request.method == "GET":
        try:
            return JsonResponse({"ok": True, **load_skill_source(skill_id)})
        except FileNotFoundError:
            return JsonResponse({"ok": False, "error": "not found"}, status=404)
        except ValueError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:  # noqa: BLE001
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)
    body = parse_json_body(request, strict=False) or {}
    files = body.get("files") or {}
    if not isinstance(files, dict):
        return JsonResponse({"ok": False, "error": "files must be an object"}, status=400)
    try:
        result = save_skill_source(
            skill_id,
            skill_md=str(body.get("skill_md") or ""),
            files={str(k): str(v) for k, v in files.items()},
        )
        status = 200 if result.get("ok") else 400
        return JsonResponse(result, status=status)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)


@require_http_methods(["GET", "POST"])
def api_tool_source(request: HttpRequest, tool_id: str) -> JsonResponse:
    if request.method == "GET":
        try:
            return JsonResponse({"ok": True, **load_tool_source(tool_id)})
        except FileNotFoundError:
            return JsonResponse({"ok": False, "error": "not found"}, status=404)
        except ValueError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:  # noqa: BLE001
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)
    body = parse_json_body(request, strict=False) or {}
    try:
        return JsonResponse(
            save_tool_source(
                tool_id,
                yaml_text=str(body.get("yaml") or body.get("text") or ""),
                install_script=str(body.get("install_script") or ""),
            )
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)


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
    path("api/skills/", api_skills, name="catalog_api_skills"),
    path("api/skills/reload/", api_skills_reload, name="catalog_api_skills_reload"),
    path(
        "api/skills/<path:skill_id>/source/",
        api_skill_source,
        name="catalog_api_skill_source",
    ),
    path("api/skills/<path:skill_id>/", api_skill_detail, name="catalog_api_skill"),
    path("api/tools/", api_tools, name="catalog_api_tools"),
    path("api/tools/reload/", api_tools_reload, name="catalog_api_tools_reload"),
    path(
        "api/tools/<str:tool_id>/source/",
        api_tool_source,
        name="catalog_api_tool_source",
    ),
    path("api/tools/<str:tool_id>/", api_tool_detail, name="catalog_api_tool"),
    path("api/resolve/", api_resolve, name="catalog_api_resolve"),
]
