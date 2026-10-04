"""Learn page — suggest / edit / save tools & roles; isolated install lab."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import yaml
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import path
from django.views.decorators.http import require_GET, require_http_methods

from orchestrator.crew.roles.registry import RoleRegistry
from orchestrator.learn.role_draft import lint_role_pack
from orchestrator.tools.catalog import ToolCatalog
from orchestrator.utils.llm import LLM_NOT_CONFIGURED, llm_configured
from orchestrator.utils.paths import write_rel_files
from orchestrator.utils.strings import require_kebab_slug
from peon.projects.catalog_cards.tool import ToolCards
from peon.projects.http_helpers import parse_json_body, split_csv
from peon.projects.learn_lab import LearnLab
from peon.projects.llm_proxy import LlmProxy
from peon.projects.role_authoring import PROXY_REQUIRED_MSG, RoleAuthoring
from peon.projects.tasks import enqueue_learn_lab_cleanup


def _parse_body(request: HttpRequest) -> dict:
    if request.content_type and "application/json" in request.content_type:
        return parse_json_body(request, strict=True)
    return {
        "prompt": request.POST.get("prompt") or "",
        "tools": request.POST.getlist("tools") or [],
    }


def _proxy_gate() -> JsonResponse | None:
    if LlmProxy.intent_enabled():
        return None
    return JsonResponse(
        {"ok": False, "error": PROXY_REQUIRED_MSG, "llm_proxy_enabled": False},
        status=403,
    )


def _roles_dir() -> Path:
    return Path(getattr(settings, "ROLES_DIR", settings.BASE_DIR / "roles")).resolve()


def _files_map(body: dict) -> dict[str, str]:
    files = body.get("files") or {}
    if isinstance(files, str):
        files = json.loads(files or "{}")
    if not isinstance(files, dict):
        raise ValueError("files must be an object")
    return {str(k): str(v) for k, v in files.items()}


def _lint_payload(checked: dict[str, Any]) -> dict[str, Any]:
    return {
        "compatible": bool(checked.get("compatible")),
        "errors": checked.get("errors") or [],
        "warnings": checked.get("warnings") or [],
    }


def _json_try(fn: Callable[[], JsonResponse]) -> JsonResponse:
    try:
        return fn()
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)


def _require_llm_proxy() -> JsonResponse | None:
    if not llm_configured():
        return JsonResponse({"ok": False, "error": LLM_NOT_CONFIGURED}, status=503)
    return _proxy_gate()


@require_GET
def learn_page(request: HttpRequest) -> HttpResponse:
    proxy = LlmProxy.shared().snapshot()
    return render(
        request,
        "learn/index.html",
        {
            "nav": "learn",
            "llm_ready": llm_configured(),
            "llm_module": str(getattr(settings, "LLM_MODULE", "litellm") or "litellm"),
            "llm_proxy_enabled": proxy["enabled"],
            "llm_proxy_running": proxy["running"],
            "llm_proxy_url": proxy["url"],
            "authoring_ready": bool(llm_configured() and proxy["enabled"]),
            "tools": ToolCards.summaries(),
            "lab": LearnLab.shared().status(),
        },
    )


@require_http_methods(["POST"])
def learn_suggest_tool(request: HttpRequest) -> JsonResponse:
    gated = _require_llm_proxy()
    if gated is not None:
        return gated

    def _run() -> JsonResponse:
        body = _parse_body(request)
        result = RoleAuthoring.shared().suggest_tool(str(body.get("prompt") or ""))
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

    return _json_try(_run)


@require_http_methods(["POST"])
def learn_write_role(request: HttpRequest) -> JsonResponse:
    gated = _require_llm_proxy()
    if gated is not None:
        return gated

    def _run() -> JsonResponse:
        body = _parse_body(request)
        tools = body.get("tools") or []
        if isinstance(tools, str):
            tools = split_csv(tools)
        result = RoleAuthoring.shared().write_role(
            str(body.get("prompt") or ""), tools=list(tools)
        )
        return JsonResponse({"ok": True, "mode": "role", **result})

    return _json_try(_run)


@require_http_methods(["POST"])
def learn_save_tool(request: HttpRequest) -> JsonResponse:
    def _run() -> JsonResponse:
        body = _parse_body(request)
        yaml_text = str(body.get("yaml") or "").strip()
        script = str(body.get("install_script") or "").strip()
        if not yaml_text:
            raise ValueError("yaml is required")
        raw = yaml.safe_load(yaml_text)
        if not isinstance(raw, dict):
            raise ValueError("tool YAML must be a mapping")
        tid = require_kebab_slug(str(raw.get("id") or body.get("id") or ""), kind="tool id")
        catalog = Path(settings.TOOLS_CATALOG_DIR)
        catalog.mkdir(parents=True, exist_ok=True)
        path = catalog / f"{tid}.yaml"
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

    return _json_try(_run)


@require_http_methods(["POST"])
def learn_test_tool(request: HttpRequest) -> JsonResponse:
    def _run() -> JsonResponse:
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

    return _json_try(_run)


@require_http_methods(["POST"])
def learn_replan_tool(request: HttpRequest) -> JsonResponse:
    gated = _require_llm_proxy()
    if gated is not None:
        return gated

    def _run() -> JsonResponse:
        body = _parse_body(request)
        result = RoleAuthoring.shared().replan_tool(
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

    return _json_try(_run)


@require_GET
def learn_lab_status(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"ok": True, "lab": LearnLab.shared().status()})


@require_http_methods(["POST"])
def learn_lab_create(request: HttpRequest) -> JsonResponse:
    def _run() -> JsonResponse:
        return JsonResponse(LearnLab.shared().create())

    return _json_try(_run)


@require_http_methods(["POST"])
def learn_lab_delete(request: HttpRequest) -> JsonResponse:
    """Queue Learn-lab removal on the worker (avoids web docker-rm flaps)."""

    def _run() -> JsonResponse:

        result = enqueue_learn_lab_cleanup()
        if "lab" not in result:
            result["lab"] = LearnLab.shared().status()
        return JsonResponse(result)

    try:
        return _run()
    except Exception as exc:  # noqa: BLE001
        return JsonResponse(
            {"ok": False, "error": str(exc), "lab": LearnLab.shared().status()},
            status=500,
        )


@require_http_methods(["POST"])
def learn_lint_role(request: HttpRequest) -> JsonResponse:

    def _run() -> JsonResponse:
        body = _parse_body(request)
        files = _files_map(body)
        name = str(body.get("name") or "").strip()
        role_yaml = str(body.get("role_yaml") or "").strip()
        checked = lint_role_pack(name=name, role_yaml=role_yaml, files=files)
        return JsonResponse(
            {"ok": True, "compatible": bool(checked.get("compatible")), "lint": _lint_payload(checked)}
        )

    return _json_try(_run)


@require_http_methods(["POST"])
def learn_save_role(request: HttpRequest) -> JsonResponse:

    def _run() -> JsonResponse:
        body = _parse_body(request)
        name = require_kebab_slug(str(body.get("name") or ""), kind="role name")
        role_yaml = str(body.get("role_yaml") or "").strip()
        files = _files_map(body)
        if not role_yaml:
            raise ValueError("role_yaml is required")
        checked = lint_role_pack(name=name, role_yaml=role_yaml, files=files)
        if not checked.get("compatible"):
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Role failed lint — fix issues before saving.",
                    "lint": _lint_payload(checked),
                },
                status=400,
            )
        root = _roles_dir() / name
        root.mkdir(parents=True, exist_ok=True)
        (root / "ROLE.yaml").write_text(role_yaml.rstrip() + "\n", encoding="utf-8")
        written = ["ROLE.yaml", *write_rel_files(root, files, skip={"ROLE.yaml", "ROLE.yml"})]
        try:
            RoleRegistry.shared().invalidate()
        except Exception:  # noqa: BLE001
            pass
        return JsonResponse(
            {
                "ok": True,
                "path": str(root),
                "name": name,
                "files": written,
                "lint": _lint_payload({**checked, "errors": []}),
            }
        )

    return _json_try(_run)


urlpatterns = [
    path("", learn_page, name="learn"),
    path("api/tool/", learn_suggest_tool, name="learn_api_tool"),
    path("api/tool/save/", learn_save_tool, name="learn_api_tool_save"),
    path("api/tool/test/", learn_test_tool, name="learn_api_tool_test"),
    path("api/tool/replan/", learn_replan_tool, name="learn_api_tool_replan"),
    path("api/role/", learn_write_role, name="learn_api_role"),
    path("api/role/lint/", learn_lint_role, name="learn_api_role_lint"),
    path("api/role/save/", learn_save_role, name="learn_api_role_save"),
    path("api/lab/", learn_lab_status, name="learn_api_lab"),
    path("api/lab/create/", learn_lab_create, name="learn_api_lab_create"),
    path("api/lab/delete/", learn_lab_delete, name="learn_api_lab_delete"),
]
