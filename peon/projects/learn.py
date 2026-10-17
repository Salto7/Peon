"""Toolsmith — draft catalog tools and skills, test installs, save packs."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import path
from django.views.decorators.http import require_GET, require_http_methods

from orchestrator.tools.catalog import ToolCatalog
from orchestrator.utils.llm import LLM_NOT_CONFIGURED, llm_configured
from orchestrator.utils.strings import is_kebab
from peon.projects.catalog_cards.tool import ToolCards
from peon.projects.http_helpers import json_errors, parse_json_body, split_csv
from peon.projects.learn_lab import LearnLab
from peon.projects.llm_proxy import LlmProxy
from peon.projects.opencode import PROXY_REQUIRED_MSG, OpenCodeAuthoring


def _parse_body(request: HttpRequest) -> dict:
    if request.content_type and "application/json" in request.content_type:
        return parse_json_body(request, strict=True)
    return {
        "prompt": request.POST.get("prompt") or "",
        "tools": request.POST.getlist("tools") or [],
    }


def _safe_slug(value: str, *, kind: str = "id") -> str:
    text = (value or "").strip().lower()
    if not is_kebab(text):
        raise ValueError(f"invalid {kind} {value!r}")
    return text


def _file_map(raw: object) -> dict[str, str]:
    if isinstance(raw, str):
        raw = json.loads(raw or "{}")
    if not isinstance(raw, dict):
        raise ValueError("files must be an object")
    return {str(key): str(value) for key, value in raw.items()}


def _authoring_gate() -> JsonResponse | None:
    if not llm_configured():
        return JsonResponse({"ok": False, "error": LLM_NOT_CONFIGURED}, status=503)
    return _proxy_gate()


def _proxy_gate() -> JsonResponse | None:
    """Block skill/tool creation when LiteLLM proxy intent is off."""
    if LlmProxy.intent_enabled():
        return None
    return JsonResponse(
        {
            "ok": False,
            "error": PROXY_REQUIRED_MSG,
            "llm_proxy_enabled": False,
        },
        status=403,
    )


@require_GET
def learn_page(request: HttpRequest) -> HttpResponse:
    proxy = LlmProxy.shared().snapshot()
    return render(
        request,
        "learn/index.html",
        {
            "nav": "lab",
            "lab_section": "toolsmith",
            "llm_ready": llm_configured(),
            "llm_proxy_enabled": proxy["enabled"],
            "llm_proxy_running": proxy["running"],
            "llm_proxy_url": proxy["url"],
            "authoring_ready": bool(llm_configured() and proxy["enabled"]),
            "tools": ToolCards.summaries(),
            "lab": LearnLab.shared().status(),
        },
    )


@require_http_methods(["POST"])
@json_errors
def learn_suggest_tool(request: HttpRequest) -> JsonResponse:
    gated = _authoring_gate()
    if gated is not None:
        return gated
    body = _parse_body(request)
    result = OpenCodeAuthoring.shared().suggest_tool(str(body.get("prompt") or ""))
    return JsonResponse(
        {
            "ok": True,
            "mode": "tool",
            "id": result["id"],
            "yaml": result["yaml"],
            "install_script": result.get("install_script") or "",
            "notes": result.get("notes") or "",
            "author": result.get("author") or "opencode",
        }
    )


@require_http_methods(["POST"])
@json_errors
def learn_write_skill(request: HttpRequest) -> JsonResponse:
    gated = _authoring_gate()
    if gated is not None:
        return gated
    body = _parse_body(request)
    tools = body.get("tools") or []
    if isinstance(tools, str):
        tools = split_csv(tools)
    result = OpenCodeAuthoring.shared().write_skill(
        str(body.get("prompt") or ""), tools=list(tools)
    )
    return JsonResponse({"ok": True, "mode": "skill", **result})


@require_http_methods(["POST"])
@json_errors
def learn_save_tool(request: HttpRequest) -> JsonResponse:
    body = _parse_body(request)
    yaml_text = str(body.get("yaml") or "").strip()
    script = str(body.get("install_script") or "").strip()
    if not yaml_text:
        raise ValueError("yaml is required")
    raw = yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        raise ValueError("tool YAML must be a mapping")
    tid = _safe_slug(str(raw.get("id") or body.get("id") or ""), kind="tool id")
    catalog = Path(settings.TOOLS_CATALOG_DIR)
    catalog.mkdir(parents=True, exist_ok=True)
    path = catalog / f"{tid}.yaml"
    # Toolsmith only ever drafts brand-new tools (there is no "load an
    # existing tool" flow here, unlike the Catalog editor) — a collision
    # means the LLM's chosen id already belongs to someone else's tool,
    # so silently overwriting it would clobber unrelated work.
    if path.is_file() and not bool(body.get("overwrite")):
        return JsonResponse(
            {
                "ok": False,
                "exists": True,
                "id": tid,
                "error": f"A tool named '{tid}' already exists in the catalog.",
            },
            status=409,
        )
    path.write_text(yaml_text.rstrip() + "\n", encoding="utf-8")
    script_path = catalog / f"{tid}.sh"
    if script:
        script_path.write_text(script.rstrip() + "\n", encoding="utf-8")
    elif script_path.is_file():
        script_path.unlink()
    ToolCatalog.shared().invalidate()
    return JsonResponse(
        {
            "ok": True,
            "path": str(path),
            "script_path": str(script_path) if script else "",
            "id": tid,
        }
    )


@require_http_methods(["POST"])
@json_errors
def learn_test_tool(request: HttpRequest) -> JsonResponse:
    body = _parse_body(request)
    result = LearnLab.shared().test_tool_install(
        yaml_text=str(body.get("yaml") or ""),
        install_script=str(body.get("install_script") or ""),
    )
    return JsonResponse(
        {
            "ok": True,
            "install_ok": bool(result.get("ok")),
            "verified": bool(result.get("verified")),
            "message": result.get("message") or "",
            "log": result.get("log") or "",
            "verify_output": result.get("verify_output") or "",
            "verify_steps": result.get("verify_steps") or [],
            "lab": result.get("lab") or LearnLab.shared().status(),
            "tool_id": result.get("tool_id") or "",
        }
    )


@require_http_methods(["POST"])
@json_errors
def learn_replan_tool(request: HttpRequest) -> JsonResponse:
    gated = _authoring_gate()
    if gated is not None:
        return gated
    body = _parse_body(request)
    result = OpenCodeAuthoring.shared().replan_tool(
        prompt=str(body.get("prompt") or ""),
        yaml_text=str(body.get("yaml") or ""),
        install_script=str(body.get("install_script") or ""),
        error=str(body.get("error") or body.get("message") or ""),
    )
    return JsonResponse(
        {
            "ok": True,
            "id": result["id"],
            "yaml": result["yaml"],
            "install_script": result.get("install_script") or "",
            "notes": result.get("notes") or "",
            "author": result.get("author") or "opencode",
        }
    )


@require_GET
def learn_lab_status(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"ok": True, "lab": LearnLab.shared().status()})


@require_http_methods(["POST"])
def learn_lab_create(request: HttpRequest) -> JsonResponse:
    try:
        result = LearnLab.shared().create()
        return JsonResponse(result)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse(
            {
                "ok": False,
                "error": str(exc),
                "lab": LearnLab.shared().status(),
            },
            status=500,
        )


@require_http_methods(["POST"])
def learn_lab_delete(request: HttpRequest) -> JsonResponse:
    """Queue Learn-lab removal on the worker (avoids web docker-rm flaps)."""
    try:
        from peon.projects.tasks import enqueue_learn_lab_cleanup

        result = enqueue_learn_lab_cleanup()
        if "lab" not in result:
            result["lab"] = LearnLab.shared().status()
        return JsonResponse(result)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse(
            {
                "ok": False,
                "error": str(exc),
                "lab": LearnLab.shared().status(),
            },
            status=500,
        )


@require_http_methods(["POST"])
@json_errors
def learn_lint_skill(request: HttpRequest) -> JsonResponse:
    body = _parse_body(request)
    name = str(body.get("name") or "").strip()
    file_map_in = _file_map(body.get("files") or {})
    from orchestrator.learn.draft import draft_binary
    from orchestrator.skills.author import prepare_claude_skill

    raw_md = str(body.get("skill_md") or "")
    prepared = prepare_claude_skill(
        name=name,
        skill_md=raw_md,
        files=file_map_in,
        binary=draft_binary(raw_md),
    )
    skill_md = str(prepared["skill_md"])
    file_map = dict(prepared["files"])
    checked = dict(prepared["lint"])
    return JsonResponse(
        {
            "ok": True,
            "compatible": bool(checked.get("compatible")),
            "name": name.lower(),
            "skill_md": skill_md,
            "files": file_map,
            "lint": {
                "compatible": bool(checked.get("compatible")),
                "errors": checked.get("errors") or [],
                "warnings": checked.get("warnings") or [],
            },
        }
    )


@require_http_methods(["POST"])
@json_errors
def learn_save_skill(request: HttpRequest) -> JsonResponse:
    from orchestrator.skills.author import save_claude_skill

    body = _parse_body(request)
    name = _safe_slug(str(body.get("name") or ""), kind="skill name")
    skill_md = str(body.get("skill_md") or "").strip()
    if not skill_md:
        raise ValueError("skill_md is required")
    from orchestrator.learn.draft import draft_binary

    saved = save_claude_skill(
        name=name,
        skill_md=skill_md,
        files=_file_map(body.get("files") or {}),
        source="toolsmith",
        binary=draft_binary(skill_md),
    )
    status = 200 if saved.get("ok") else 400
    return JsonResponse(saved, status=status)


urlpatterns = [
    path("", learn_page, name="lab_toolsmith"),
    path("api/tool/", learn_suggest_tool, name="learn_api_tool"),
    path("api/tool/save/", learn_save_tool, name="learn_api_tool_save"),
    path("api/tool/test/", learn_test_tool, name="learn_api_tool_test"),
    path("api/tool/replan/", learn_replan_tool, name="learn_api_tool_replan"),
    path("api/skill/", learn_write_skill, name="learn_api_skill"),
    path("api/skill/lint/", learn_lint_skill, name="learn_api_skill_lint"),
    path("api/skill/save/", learn_save_skill, name="learn_api_skill_save"),
    path("api/lab/", learn_lab_status, name="learn_api_lab"),
    path("api/lab/create/", learn_lab_create, name="learn_api_lab_create"),
    path("api/lab/delete/", learn_lab_delete, name="learn_api_lab_delete"),
]
