"""Catalog HTTP + role/tool card projections (RoleRegistry / ToolCatalog)."""

from __future__ import annotations

import json

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseNotFound, JsonResponse
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from orchestrator.crew.roles.catalog import ROLE_KINDS
from orchestrator.crew.router import RoleRouter
from orchestrator.crew.roles.registry import RoleRegistry
from orchestrator.tools.catalog import ToolCatalog
from peon.projects.catalog_cards import (
    CatalogCardsBase,
    McpCards,
    RoleCards,
    SkillCards,
    ToolCards,
)
from peon.projects.catalog_edit import (
    load_skill_source,
    load_tool_source,
    save_skill_source,
    save_tool_source,
)
from orchestrator.utils.strings import as_str_list
from peon.projects import marketplace_ops
from peon.projects.http_helpers import json_errors, parse_json_body
from peon.projects.platform_catalog import PlatformCatalog
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


# Source choices for the install-form island (first entry is the default).
INSTALL_SOURCES = {
    "skill": [
        {"value": "git", "label": "Git repository", "placeholder": "owner/repo or https://github.com/org/repo.git"},
        {"value": "npx", "label": "openskills", "placeholder": "owner/repo"},
        {"value": "disk", "label": "Local folder"},
    ],
    "mcp": [
        {"value": "npx", "label": "npx package", "placeholder": "@modelcontextprotocol/server-github"},
        {"value": "git", "label": "Git repository", "placeholder": "owner/repo or https://github.com/org/repo.git"},
        {"value": "disk", "label": "Local folder"},
    ],
}


@require_GET
def catalog_page(request: HttpRequest) -> HttpResponse:
    show_all = request.GET.get("all", "").lower() in {"1", "true", "yes"}

    def _reload_ctx(cards: type[CatalogCardsBase]) -> dict[str, str]:
        btn = cards.reload_button()
        return {**btn, "url": reverse(btn["url_name"])}

    edit_urls = {
        "skill_source": "/catalog/api/skills/{id}/source/",
        "skill_delete": "/catalog/api/skills/{id}/delete/",
        "tool_source": "/catalog/api/tools/{id}/source/",
        "mcp_source": "/catalog/api/mcp/{id}/",
        "role_source": "/catalog/api/roles/{id}/",
        "role_write": "/catalog/api/roles/{id}/edit/",
        "role_create": reverse("catalog_api_role_create"),
    }
    marketplace_urls = {
        "delete": "/catalog/api/marketplace/{id}/",
        "scan": "/catalog/api/marketplace/{id}/scan/",
        "import": "/catalog/api/marketplace/{id}/import/",
    }
    # Proposals (Dream-suggested or marketplace-discovered role/skill changes
    # pending review) are reviewed here, not on the Dream page.
    proposal_urls = {
        "list": reverse("catalog_api_proposals"),
        "decide": reverse("catalog_api_proposal_decide"),
    }
    catalog = PlatformCatalog.shared()
    return render(
        request,
        "catalog/list.html",
        {
            "roles": RoleCards.catalog(jobable_only=not show_all),
            "skills": SkillCards.catalog(),
            "mcps": [_mcp_card(rec) for rec in catalog.list_mcps()],
            "tools": ToolCards.catalog(),
            "show_all": show_all,
            "roles_reload": _reload_ctx(RoleCards),
            "skills_reload": _reload_ctx(SkillCards),
            "mcp_reload": _reload_ctx(McpCards),
            "tools_reload": _reload_ctx(ToolCards),
            "catalog_edit_urls": edit_urls,
            "role_kinds": sorted(ROLE_KINDS),
            "role_names": sorted(r["name"] for r in RoleCards.catalog(jobable_only=False)),
            "skill_sources": INSTALL_SOURCES["skill"],
            "mcp_sources": INSTALL_SOURCES["mcp"],
            "marketplace_sources": marketplace_ops.list_sources(),
            "marketplace_urls": marketplace_urls,
            "proposal_urls": proposal_urls,
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


@require_POST
def api_mcp_reload(request: HttpRequest) -> HttpResponse:
    """Force-rescan ``mcps/`` into the platform catalog."""
    return _catalog_reload(request, McpCards)


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


def _source_response(load, item_id: str) -> JsonResponse:
    try:
        return JsonResponse({"ok": True, **load(item_id)})
    except FileNotFoundError:
        return JsonResponse({"ok": False, "error": "not found"}, status=404)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001 — surfaced to the editor
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)


@require_http_methods(["GET", "POST"])
@json_errors
def api_skill_source(request: HttpRequest, skill_id: str) -> JsonResponse:
    if request.method == "GET":
        return _source_response(load_skill_source, skill_id)
    body = parse_json_body(request, strict=False) or {}
    files = body.get("files") or {}
    if not isinstance(files, dict):
        return JsonResponse({"ok": False, "error": "files must be an object"}, status=400)
    bindings = body.get("bindings")
    if bindings is not None and not isinstance(bindings, list):
        return JsonResponse({"ok": False, "error": "bindings must be a list"}, status=400)
    result = save_skill_source(
        skill_id,
        skill_md=str(body.get("skill_md") or ""),
        files={str(k): str(v) for k, v in files.items()},
        bindings=bindings,
    )
    status = 200 if result.get("ok") else 400
    return JsonResponse(result, status=status)


@require_http_methods(["GET", "POST"])
@json_errors
def api_tool_source(request: HttpRequest, tool_id: str) -> JsonResponse:
    if request.method == "GET":
        return _source_response(load_tool_source, tool_id)
    body = parse_json_body(request, strict=False) or {}
    return JsonResponse(
        save_tool_source(
            tool_id,
            yaml_text=str(body.get("yaml") or body.get("text") or ""),
            install_script=str(body.get("install_script") or ""),
        )
    )


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


def _mcp_card(rec) -> dict:
    return {
        "id": rec.mcp_id,
        "description": rec.description,
        "command": rec.command,
        "enabled": rec.enabled,
        "env": [name for name, _required in rec.env_schema],
        "bindings": [
            {"role_id": b.role_id, "score": b.score, "lead": b.lead}
            for b in rec.bindings
        ],
    }


def _bindings_from_body(body: dict) -> list[dict] | None:
    raw = body.get("bindings")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("bindings must be a list")
    return list(raw)


@require_http_methods(["GET", "POST", "DELETE"])
def api_role_write(request: HttpRequest, name: str) -> JsonResponse:
    catalog = PlatformCatalog.shared()
    if request.method == "GET":
        role = catalog.get_role(name)
        if role is None:
            return JsonResponse({"ok": False, "error": "not found"}, status=404)
        bindings = [
            {
                "role_id": b.role_id,
                "owner_kind": b.owner_kind,
                "owner_id": b.owner_id,
                "score": b.score,
                "lead": b.lead,
            }
            for b in catalog.list_bindings(role_id=role.id)
        ]
        return JsonResponse(
            {
                "ok": True,
                "id": role.id,
                "kind": role.kind,
                "aliases": list(role.aliases),
                "protected": role.is_protected,
                "goal": role.goal,
                "backstory": role.backstory,
                "bindings": bindings,
            }
        )
    if request.method == "DELETE":
        try:
            catalog.delete_role(name)
        except KeyError:
            return JsonResponse({"ok": False, "error": "not found"}, status=404)
        except ValueError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        return JsonResponse({"ok": True, "id": name})
    body = parse_json_body(request, strict=False) or {}
    try:
        saved = catalog.upsert_role(
            role_id=str(body.get("role_id") or name),
            kind=str(body.get("kind") or ""),
            aliases=body.get("aliases") or [],
            protected=body.get("protected") if "protected" in body else None,
            previous_id="" if name in {"", "__new__"} else name,
            goal=str(body.get("goal") or ""),
            backstory=str(body.get("backstory") or ""),
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "id": saved.id, "kind": saved.kind})


@require_http_methods(["POST"])
def api_role_create(request: HttpRequest) -> JsonResponse:
    body = parse_json_body(request, strict=False) or {}
    try:
        saved = PlatformCatalog.shared().upsert_role(
            role_id=str(body.get("role_id") or ""),
            kind=str(body.get("kind") or "technical"),
            aliases=body.get("aliases") or [],
            goal=str(body.get("goal") or ""),
            backstory=str(body.get("backstory") or ""),
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "id": saved.id})


@require_http_methods(["DELETE"])
def api_skill_delete(request: HttpRequest, skill_id: str) -> JsonResponse:
    try:
        PlatformCatalog.shared().delete_skill(skill_id)
    except KeyError:
        return JsonResponse({"ok": False, "error": "not found"}, status=404)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "id": skill_id})


@require_http_methods(["GET", "POST", "DELETE"])
def api_mcp_detail(request: HttpRequest, mcp_id: str) -> JsonResponse:
    catalog = PlatformCatalog.shared()
    if request.method == "DELETE":
        try:
            catalog.delete_mcp(mcp_id)
        except KeyError:
            return JsonResponse({"ok": False, "error": "not found"}, status=404)
        return JsonResponse({"ok": True, "id": mcp_id})
    if request.method == "GET":
        if mcp_id in {"", "__new__"}:
            return JsonResponse(
                {
                    "ok": True,
                    "id": "",
                    "document": {
                        "name": "",
                        "description": "",
                        "transport": "stdio",
                    "command": "",
                    "args": [],
                    "cwd": "",
                    "env_schema": [],
                    },
                    "bindings": [],
                    "files": {},
                }
            )
        rec = catalog.get_mcp(mcp_id)
        if rec is None:
            return JsonResponse({"ok": False, "error": "not found"}, status=404)
        return JsonResponse(
            {
                "ok": True,
                "id": rec.mcp_id,
                "enabled": rec.enabled,
                "document": {
                    "name": rec.mcp_id,
                    "description": rec.description,
                    "transport": rec.transport,
                    "command": rec.command,
                    "args": list(rec.args),
                    "cwd": rec.cwd,
                    "install": rec.install,
                    "env_schema": list(rec.env_schema_full),
                },
                "files": rec.files,
                "bindings": [
                    {"role_id": b.role_id, "score": b.score, "lead": b.lead}
                    for b in rec.bindings
                ],
            }
        )
    body = parse_json_body(request, strict=False) or {}
    document = body.get("document") or body.get("spec") or body
    files = body.get("files") or {}
    if not isinstance(files, dict):
        return JsonResponse({"ok": False, "error": "files must be an object"}, status=400)
    try:
        bindings = _bindings_from_body(body)
        saved = catalog.save_mcp_document(
            document,
            files={str(k): str(v) for k, v in files.items()},
            bindings=bindings,
            enabled=body.get("enabled") if "enabled" in body else None,
            rename_from="" if mcp_id in {"", "__new__"} else mcp_id,
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse(
        {"ok": True, "id": saved[-1].mcp_id, "ids": [rec.mcp_id for rec in saved]}
    )


def _install_request(request: HttpRequest) -> tuple[str, str, str]:
    body = parse_json_body(request, strict=False) or {}
    kind = str(
        body.get("kind") or request.POST.get("kind") or ""
    ).strip().lower()
    value = str(
        body.get("source")
        or body.get("value")
        or request.POST.get("source")
        or request.POST.get("value")
        or ""
    ).strip()
    revision = str(
        body.get("revision") or request.POST.get("revision") or ""
    ).strip()
    return kind, value, revision


_MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _wants_json(request: HttpRequest) -> bool:
    return (
        "application/json" in (request.headers.get("Accept") or "")
        or (request.content_type or "").startswith("application/json")
    )


def _upload_pairs(request: HttpRequest) -> list[tuple[str, bytes]]:
    uploads = request.FILES.getlist("file")
    paths = [str(item) for item in request.POST.getlist("path")]
    if not uploads:
        raise ValueError("Choose a folder (or drop files) first")
    total = sum(int(item.size or 0) for item in uploads)
    if total > _MAX_UPLOAD_BYTES:
        raise ValueError("Upload is larger than 20 MB")
    if len(paths) != len(uploads):
        raise ValueError("each uploaded file needs a relative path")
    return [
        (rel, upload.read())
        for rel, upload in zip(paths, uploads, strict=True)
    ]


def _install_response(request: HttpRequest, run, *, label: str, anchor: str) -> HttpResponse:
    """Run an installer; answer JSON for XHR, else flash a message and redirect."""
    wants_json = _wants_json(request)
    try:
        ids = run()
    except (RuntimeError, ValueError, OSError) as exc:
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, str(exc))
    else:
        if wants_json:
            return JsonResponse({"ok": True, "ids": ids})
        messages.success(request, f"Installed {label}(s): " + ", ".join(ids))
    return redirect(reverse("catalog") + f"#{anchor}")


def _ingest_skills(names: list[str]) -> list[str]:
    catalog = PlatformCatalog.shared()
    ids = [catalog.ingest_imported_skill(name) for name in names]
    SkillRegistry.shared().invalidate()
    return ids or names


@require_POST
def api_skills_install(request: HttpRequest) -> HttpResponse:
    """Install a skill pack from disk, git, or openskills/npx."""
    from orchestrator.skills.install import install_skill_source

    kind, value, revision = _install_request(request)
    return _install_response(
        request,
        lambda: _ingest_skills(install_skill_source(kind, value, revision=revision)),
        label="skill",
        anchor="skills",
    )


@require_POST
def api_skills_upload(request: HttpRequest) -> HttpResponse:
    """Install a skill pack from a browser folder picker upload or a single .zip."""
    from orchestrator.skills.install.dragdrop import install_relative_files, install_zip

    def run() -> list[str]:
        uploads = request.FILES.getlist("file")
        if len(uploads) == 1 and (uploads[0].name or "").lower().endswith(".zip"):
            if int(uploads[0].size or 0) > _MAX_UPLOAD_BYTES:
                raise ValueError("Upload is larger than 20 MB")
            return _ingest_skills(install_zip(uploads[0].read(), filename=uploads[0].name))
        return _ingest_skills(
            install_relative_files(_upload_pairs(request), origin="catalog-upload")
        )

    return _install_response(request, run, label="skill", anchor="skills")


@require_POST
def api_mcp_install(request: HttpRequest) -> HttpResponse:
    """Install an MCP server from disk, git, or npx package into the catalog."""
    from orchestrator.mcp.install import install_mcp_source

    kind, value, revision = _install_request(request)
    return _install_response(
        request,
        lambda: install_mcp_source(kind, value, revision=revision),
        label="MCP server",
        anchor="mcp",
    )


@require_POST
def api_mcp_upload(request: HttpRequest) -> HttpResponse:
    """Install an MCP pack from a browser folder picker upload."""
    from orchestrator.mcp.install import install_mcp_relative_files

    return _install_response(
        request,
        lambda: install_mcp_relative_files(_upload_pairs(request), origin="catalog-upload"),
        label="MCP server",
        anchor="mcp",
    )


# ── Marketplace: register a source, scan it, import reviewed items ─────────


@require_http_methods(["GET", "POST"])
def api_marketplace_sources(request: HttpRequest) -> JsonResponse:
    if request.method == "GET":
        return JsonResponse({"ok": True, "sources": marketplace_ops.list_sources()})
    body = parse_json_body(request, strict=False) or {} if _wants_json(request) else {}
    archive_bytes = None
    if request.FILES.get("file"):
        upload = request.FILES["file"]
        if int(upload.size or 0) > _MAX_UPLOAD_BYTES:
            return JsonResponse({"ok": False, "error": "Upload is larger than 20 MB"}, status=400)
        archive_bytes = upload.read()
    try:
        source = marketplace_ops.create_source(
            name=body.get("name") or request.POST.get("name") or "",
            source_type=body.get("source_type") or request.POST.get("source_type") or "",
            url=body.get("url") or request.POST.get("url") or "",
            ref=body.get("ref") or request.POST.get("ref") or "",
            subpath=body.get("subpath") or request.POST.get("subpath") or "",
            archive_bytes=archive_bytes,
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "source": source})


@require_http_methods(["DELETE"])
def api_marketplace_source_delete(request: HttpRequest, source_id: str) -> JsonResponse:
    try:
        marketplace_ops.delete_source(source_id)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=404)
    return JsonResponse({"ok": True})


@require_POST
def api_marketplace_scan(request: HttpRequest, source_id: str) -> JsonResponse:
    try:
        source = marketplace_ops.scan_source(source_id)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)[:400]}, status=502)
    return JsonResponse({"ok": True, "source": source})


@require_POST
def api_marketplace_import(request: HttpRequest, source_id: str) -> JsonResponse:
    body = parse_json_body(request, strict=False) or {}
    item_kind = str(body.get("kind") or "").strip().lower()
    try:
        if item_kind == "skill":
            result = marketplace_ops.import_skill(source_id, path=str(body.get("path") or ""))
        elif item_kind == "mcp_pointer":
            result = marketplace_ops.import_mcp_pointer(
                source_id, repo_url=str(body.get("repo_url") or ""), ref=str(body.get("ref") or "")
            )
        elif item_kind == "agent":
            result = marketplace_ops.import_agent(
                source_id,
                name=str(body.get("name") or ""),
                description=str(body.get("description") or ""),
                instructions=str(body.get("instructions") or ""),
            )
        else:
            return JsonResponse({"ok": False, "error": "kind must be skill, mcp_pointer, or agent"}, status=400)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)[:400]}, status=502)
    return JsonResponse(result)


# ── Pending role/skill proposals (Dream-suggested or marketplace-discovered
# catalog changes) — reviewed and approved right here, not on the Dream page.
# A proposal never becomes a live role/skill until approved; nothing here
# makes an unapproved one assignable.


def _serialize_proposal(row) -> dict:
    return {
        "id": str(row.pk),
        "kind": row.kind,
        "action": row.action,
        "target_id": row.target_id,
        "title": row.title,
        "rationale": row.rationale,
        "payload": row.payload,
        "status": row.status,
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


@require_GET
def api_proposals(request: HttpRequest) -> JsonResponse:
    from peon.projects.models import CatalogProposal

    status = (request.GET.get("status") or "pending").strip().lower()
    qs = CatalogProposal.objects.all()
    if status != "all":
        qs = qs.filter(status=status)
    rows = list(qs[:50])
    return JsonResponse({"ok": True, "proposals": [_serialize_proposal(r) for r in rows]})


@require_POST
def api_proposal_decide(request: HttpRequest) -> JsonResponse:
    from peon.projects.gist import apply_proposal

    body = parse_json_body(request, strict=False) or {}
    proposal_id = str(body.get("id") or "").strip()
    decision = str(body.get("decision") or "").strip().lower()
    if not proposal_id or decision not in {"approve", "reject"}:
        return JsonResponse({"ok": False, "error": "id and decision (approve|reject) required"}, status=400)
    result = apply_proposal(proposal_id, decision=decision)
    return JsonResponse(result, status=200 if result.get("ok") else 400)


urlpatterns = [
    path("", catalog_page, name="catalog"),
    path("api/roles/", api_roles, name="catalog_api_roles"),
    path("api/roles/create/", api_role_create, name="catalog_api_role_create"),
    path("api/roles/reload/", api_roles_reload, name="catalog_api_roles_reload"),
    path("api/roles/<str:name>/edit/", api_role_write, name="catalog_api_role_write"),
    path("api/roles/<str:name>/", api_role_detail, name="catalog_api_role"),
    path("api/skills/", api_skills, name="catalog_api_skills"),
    path("api/skills/reload/", api_skills_reload, name="catalog_api_skills_reload"),
    path("api/skills/install/", api_skills_install, name="catalog_api_skills_install"),
    path("api/skills/upload/", api_skills_upload, name="catalog_api_skills_upload"),
    path(
        "api/skills/<path:skill_id>/source/",
        api_skill_source,
        name="catalog_api_skill_source",
    ),
    path(
        "api/skills/<path:skill_id>/delete/",
        api_skill_delete,
        name="catalog_api_skill_delete",
    ),
    path("api/skills/<path:skill_id>/", api_skill_detail, name="catalog_api_skill"),
    path("api/mcp/reload/", api_mcp_reload, name="catalog_api_mcp_reload"),
    path("api/mcp/install/", api_mcp_install, name="catalog_api_mcp_install"),
    path("api/mcp/upload/", api_mcp_upload, name="catalog_api_mcp_upload"),
    path("api/mcp/<str:mcp_id>/", api_mcp_detail, name="catalog_api_mcp"),
    path("api/marketplace/", api_marketplace_sources, name="catalog_api_marketplace_sources"),
    path(
        "api/marketplace/<str:source_id>/",
        api_marketplace_source_delete,
        name="catalog_api_marketplace_source_delete",
    ),
    path(
        "api/marketplace/<str:source_id>/scan/",
        api_marketplace_scan,
        name="catalog_api_marketplace_scan",
    ),
    path(
        "api/marketplace/<str:source_id>/import/",
        api_marketplace_import,
        name="catalog_api_marketplace_import",
    ),
    path("api/proposals/", api_proposals, name="catalog_api_proposals"),
    path("api/proposals/decide/", api_proposal_decide, name="catalog_api_proposal_decide"),
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
