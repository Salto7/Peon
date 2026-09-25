"""Catalog HTTP + skill/tool card projections (SkillRegistry / ToolCatalog)."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseNotFound, JsonResponse
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from orchestrator.skills.misc.registry import SkillRegistry
from orchestrator.skills.misc.router import SkillRouter
from orchestrator.skills.misc.utils import CORE_SKILLS
from orchestrator.skills.provision import SkillLinter
from orchestrator.tools.catalog import CatalogTool, ToolCatalog
from peon.projects.http_helpers import split_csv


class CatalogCards(ABC):
    """Shared helpers for projecting catalog assets into UI card dicts."""

    DESC_LIMIT = 280
    PICKER_DESC_LIMIT = 200
    TAG_LIMIT = 12
    PICKER_TAG_LIMIT = 8
    ALIAS_LIMIT = 8

    # Section toolbar / reload — subclasses set these and override ``reload``.
    SECTION = ""
    LABEL = ""
    RELOAD_URL_NAME = ""
    RELOAD_TITLE = ""

    @staticmethod
    def text(value: object, *, limit: int | None = None) -> str:
        text = str(value or "").strip()
        if limit is not None:
            return text[:limit]
        return text

    @classmethod
    def labels(cls, values: list | None, *, limit: int | None = None) -> list[str]:
        cap = cls.TAG_LIMIT if limit is None else limit
        out: list[str] = []
        for raw in values or []:
            item = str(raw).strip()
            if not item or item in out:
                continue
            out.append(item)
            if len(out) >= cap:
                break
        return out

    @classmethod
    def base(
        cls,
        *,
        name: str,
        description: str = "",
        tags: list | None = None,
        desc_limit: int | None = None,
        tag_limit: int | None = None,
    ) -> dict[str, Any]:
        """Fields shared by every catalog card."""
        return {
            "name": cls.text(name),
            "description": cls.text(
                description,
                limit=cls.DESC_LIMIT if desc_limit is None else desc_limit,
            ),
            "tags": cls.labels(tags, limit=cls.TAG_LIMIT if tag_limit is None else tag_limit),
        }

    @classmethod
    @abstractmethod
    def catalog(cls, **kwargs: Any) -> list[dict]:
        """Return card dicts for the full catalog listing."""

    @classmethod
    @abstractmethod
    def reload(cls) -> dict[str, list]:
        """Force-rescan backing store; return added/removed/modified diff."""

    @classmethod
    def reload_flash(cls, diff: dict) -> str:
        added = len(diff.get("added") or [])
        removed = len(diff.get("removed") or [])
        modified = len(diff.get("modified") or [])
        return (
            f"{cls.LABEL} updated — added {added}, removed {removed}, "
            f"modified {modified}."
        )

    @classmethod
    def reload_button(cls) -> dict[str, str]:
        """Toolbar metadata for the section Update button."""
        return {
            "url_name": cls.RELOAD_URL_NAME,
            "title": cls.RELOAD_TITLE,
            "label": f"Update {cls.SECTION}",
            "section": cls.SECTION,
        }


class SkillCards(CatalogCards):
    """UI cards for filesystem skills (SkillRegistry)."""

    SECTION = "skills"
    LABEL = "Skills"
    RELOAD_URL_NAME = "catalog_api_skills_reload"
    RELOAD_TITLE = "Rescan SKILLS_DIR via SkillRegistry.reload_skills"

    @classmethod
    def reload(cls) -> dict[str, list]:
        return SkillRegistry.shared().reload_skills()

    @classmethod
    def card(
        cls,
        *,
        name: str,
        description: str = "",
        category: str = "",
        tags: list | None = None,
        lifecycle: str = "",
        aliases: list | None = None,
        jobable: bool = True,
        required: bool = False,
        compatible: bool = True,
        lint_issues: list | None = None,
        desc_limit: int | None = None,
        tag_limit: int | None = None,
    ) -> dict:
        return {
            **cls.base(
                name=name,
                description=description,
                tags=tags,
                desc_limit=desc_limit,
                tag_limit=tag_limit,
            ),
            "category": cls.text(category),
            "lifecycle": cls.text(lifecycle),
            "aliases": cls.labels(aliases, limit=cls.ALIAS_LIMIT),
            "jobable": bool(jobable),
            "required": bool(required),
            "compatible": bool(compatible),
            "lint_issues": [cls.text(m) for m in (lint_issues or []) if str(m).strip()],
        }

    @staticmethod
    def required_names() -> list[str]:
        """Core skills always included on every project (cannot be deselected)."""
        return sorted(CORE_SKILLS)

    @classmethod
    def lint_fields(cls, skill_dir) -> tuple[bool, list[str]]:
        """Return (compatible, error messages) for catalog hazard UI."""
        checked = SkillLinter.shared().check(skill_dir)
        messages_out = [
            str(i.get("message") or "").strip()
            for i in (checked.get("errors") or [])
            if str(i.get("message") or "").strip()
        ]
        return bool(checked.get("compatible")), messages_out

    @classmethod
    def from_registry(cls, name: str) -> dict:
        skill = SkillRegistry.shared().load_skill(name)
        if skill is None:
            return cls.card(name=name)
        compatible, lint_issues = cls.lint_fields(skill.skill_dir)
        return cls.card(
            name=skill.name,
            description=skill.description or "",
            category=skill.category or "",
            tags=list(skill.tags or []),
            lifecycle=str(skill.lifecycle or ""),
            aliases=list(skill.aliases or []),
            jobable=bool(skill.jobable),
            required=bool(skill.is_builtin),
            compatible=compatible,
            lint_issues=lint_issues,
        )

    @classmethod
    def for_names(cls, names: list[str]) -> list[dict]:
        return [cls.from_registry(n) for n in names if str(n).strip()]

    @classmethod
    def catalog(cls, *, jobable_only: bool = True, **kwargs: Any) -> list[dict]:
        del kwargs
        skills = sorted(
            SkillRegistry.shared().get_registry().values(),
            key=lambda s: s.name,
        )
        out: list[dict] = []
        for s in skills:
            if jobable_only and not s.jobable:
                continue
            compatible, lint_issues = cls.lint_fields(s.skill_dir)
            out.append(
                cls.card(
                    name=s.name,
                    description=s.description or "",
                    category=s.category or "",
                    tags=list(s.tags or []),
                    lifecycle=str(s.lifecycle or ""),
                    aliases=list(s.aliases or []),
                    jobable=bool(s.jobable),
                    required=bool(s.is_builtin),
                    compatible=compatible,
                    lint_issues=lint_issues,
                )
            )
        return out

    @classmethod
    def picker(cls) -> list[dict]:
        """Jobable skills for the create-project picker (compact cards).

        Core skills (`CORE_SKILLS`) are marked ``required`` and listed first.
        """
        skills = sorted(
            SkillRegistry.shared().get_registry().values(),
            key=lambda s: (0 if s.is_builtin else 1, s.name),
        )
        return [
            cls.card(
                name=s.name,
                description=s.description or "",
                category=s.category or "",
                tags=list(s.tags or []),
                jobable=bool(s.jobable),
                required=bool(s.is_builtin),
                desc_limit=cls.PICKER_DESC_LIMIT,
                tag_limit=cls.PICKER_TAG_LIMIT,
            )
            for s in skills
            if s.jobable
        ]


class ToolCards(CatalogCards):
    """UI cards for sandbox tools (``tools/catalog`` YAML)."""

    SECTION = "tools"
    LABEL = "Tools"
    RELOAD_URL_NAME = "catalog_api_tools_reload"
    RELOAD_TITLE = "Rescan TOOLS_CATALOG_DIR via ToolCatalog.reload_tools"

    @classmethod
    def reload(cls) -> dict[str, list]:
        return ToolCatalog.shared().reload_tools()

    @classmethod
    def card(
        cls,
        *,
        id: str,
        name: str = "",
        description: str = "",
        tier: str = "",
        binary: str = "",
        binaries: list | None = None,
        skills: list | None = None,
        tags: list | None = None,
        install: list | None = None,
        desc_limit: int | None = None,
        tag_limit: int | None = None,
    ) -> dict:
        tool_id = cls.text(id)
        steps = list(install or [])
        has_command = any(
            isinstance(s, dict)
            and (
                str(s.get("type") or "").strip().lower()
                in {"custom", "command", "shell", "run", "bash", "script"}
                or (not s.get("type") and s.get("command"))
            )
            for s in steps
        )
        return {
            "id": tool_id,
            **cls.base(
                name=name or tool_id,
                description=description,
                tags=tags,
                desc_limit=desc_limit,
                tag_limit=tag_limit,
            ),
            "tier": cls.text(tier),
            "binary": cls.text(binary),
            "binaries": cls.labels(binaries),
            "skills": cls.labels(skills),
            "install_types": [
                str(s.get("type") or ("custom" if s.get("command") else "")).strip()
                for s in steps
                if isinstance(s, dict)
            ],
            "has_custom_install": has_command,
        }

    @classmethod
    def from_tool(cls, tool: CatalogTool) -> dict:
        return cls.card(
            id=tool.id,
            name=tool.name,
            description=tool.description or "",
            tier=tool.tier or "",
            binary=tool.binary or "",
            binaries=list(tool.binaries or []),
            skills=list(tool.skills or []),
            tags=list(tool.tags or []),
            install=list(tool.install or []),
        )

    @classmethod
    def catalog(cls, *, include_image: bool = True, **kwargs: Any) -> list[dict]:
        del kwargs
        tools = sorted(ToolCatalog.shared().all().values(), key=lambda t: t.id)
        out: list[dict] = []
        for t in tools:
            if not include_image and t.is_image_tier:
                continue
            out.append(cls.from_tool(t))
        return out


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
        explicit = body.get("skills") or body.get("explicit") or []
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
        split_csv(request.GET.get("skills")),
        split_csv(request.GET.get("preferred_tags") or request.GET.get("focus_tags")),
    )


@require_GET
def catalog_page(request: HttpRequest) -> HttpResponse:
    show_all = request.GET.get("all", "").lower() in {"1", "true", "yes"}

    def _reload_ctx(cards: type[CatalogCards]) -> dict[str, str]:
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
    cards: type[CatalogCards],
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
        names = SkillRouter.shared().resolve_default_skills(
            description,
            lifecycle=lifecycle,
            explicit=explicit or None,
            preferred_tags=preferred_tags or None,
            project=project,
        )
    return JsonResponse(
        {
            "skill_names": names,
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
