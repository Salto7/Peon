"""Learn page — suggest / edit / save tools & skills; isolated install lab."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import path
from django.views.decorators.http import require_GET, require_http_methods

from orchestrator.learn import LearnAuthoring
from orchestrator.skills.misc.registry import SkillRegistry
from orchestrator.tools.catalog import ToolCatalog
from peon.projects.learn_lab import LearnLab
from peon.projects.llm_gate import llm_configured

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _parse_body(request: HttpRequest) -> dict:
    if request.content_type and "application/json" in request.content_type:
        try:
            body = json.loads(request.body.decode() or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("invalid JSON") from exc
        if not isinstance(body, dict):
            raise ValueError("JSON object required")
        return body
    return {
        "prompt": request.POST.get("prompt") or "",
        "tools": request.POST.getlist("tools") or [],
    }


def _safe_slug(value: str, *, kind: str = "id") -> str:
    text = (value or "").strip().lower()
    if not text or not _SLUG.match(text):
        raise ValueError(f"invalid {kind} {value!r}")
    return text


@require_GET
def learn_page(request: HttpRequest) -> HttpResponse:
    tools = [
        {"id": t.id, "binary": t.binary or t.id, "name": t.name or t.id}
        for t in sorted(ToolCatalog.shared().all().values(), key=lambda x: x.id)
        if not t.is_image_tier
    ]
    return render(
        request,
        "learn/index.html",
        {
            "nav": "learn",
            "llm_ready": llm_configured(),
            "tools": tools,
            "lab": LearnLab.shared().status(),
            "urls": {
                "suggest_tool": "/learn/api/tool/",
                "suggest_skill": "/learn/api/skill/",
                "save_tool": "/learn/api/tool/save/",
                "test_tool": "/learn/api/tool/test/",
                "replan_tool": "/learn/api/tool/replan/",
                "lab_create": "/learn/api/lab/create/",
                "lab_delete": "/learn/api/lab/delete/",
                "lab_status": "/learn/api/lab/",
                "lint_skill": "/learn/api/skill/lint/",
                "save_skill": "/learn/api/skill/save/",
            },
        },
    )


@require_http_methods(["POST"])
def learn_suggest_tool(request: HttpRequest) -> JsonResponse:
    if not llm_configured():
        return JsonResponse(
            {"ok": False, "error": "Set OPENROUTER_API_KEY (or OpenAI/LiteLLM)."},
            status=503,
        )
    try:
        body = _parse_body(request)
        result = LearnAuthoring.shared().suggest_tool(str(body.get("prompt") or ""))
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)
    return JsonResponse(
        {
            "ok": True,
            "mode": "tool",
            "id": result["id"],
            "yaml": result["yaml"],
            "install_script": result.get("install_script") or "",
            "notes": result.get("notes") or "",
        }
    )


@require_http_methods(["POST"])
def learn_write_skill(request: HttpRequest) -> JsonResponse:
    if not llm_configured():
        return JsonResponse(
            {"ok": False, "error": "Set OPENROUTER_API_KEY (or OpenAI/LiteLLM)."},
            status=503,
        )
    try:
        body = _parse_body(request)
        tools = body.get("tools") or []
        if isinstance(tools, str):
            tools = [t.strip() for t in tools.split(",") if t.strip()]
        result = LearnAuthoring.shared().write_skill(
            str(body.get("prompt") or ""), tools=list(tools)
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)
    return JsonResponse({"ok": True, "mode": "skill", **result})


@require_http_methods(["POST"])
def learn_save_tool(request: HttpRequest) -> JsonResponse:
    try:
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
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)


@require_http_methods(["POST"])
def learn_test_tool(request: HttpRequest) -> JsonResponse:
    try:
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
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)


@require_http_methods(["POST"])
def learn_replan_tool(request: HttpRequest) -> JsonResponse:
    if not llm_configured():
        return JsonResponse(
            {"ok": False, "error": "Set OPENROUTER_API_KEY (or OpenAI/LiteLLM)."},
            status=503,
        )
    try:
        body = _parse_body(request)
        result = LearnAuthoring.shared().replan_tool(
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
            }
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)


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
def learn_lint_skill(request: HttpRequest) -> JsonResponse:
    try:
        body = _parse_body(request)
        files = body.get("files") or {}
        if isinstance(files, str):
            files = json.loads(files or "{}")
        if not isinstance(files, dict):
            raise ValueError("files must be an object")
        name = str(body.get("name") or "").strip()
        checked = LearnAuthoring.shared().lint_skill(
            name=name,
            skill_md=str(body.get("skill_md") or ""),
            files={str(k): str(v) for k, v in files.items()},
        )
        return JsonResponse(
            {
                "ok": True,
                "compatible": bool(checked.get("compatible")),
                "lint": {
                    "compatible": bool(checked.get("compatible")),
                    "errors": checked.get("errors") or [],
                    "warnings": checked.get("warnings") or [],
                },
            }
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)


@require_http_methods(["POST"])
def learn_save_skill(request: HttpRequest) -> JsonResponse:
    try:
        body = _parse_body(request)
        name = _safe_slug(str(body.get("name") or ""), kind="skill name")
        skill_md = str(body.get("skill_md") or "").strip()
        files = body.get("files") or {}
        if isinstance(files, str):
            files = json.loads(files or "{}")
        if not isinstance(files, dict):
            raise ValueError("files must be an object")
        if not skill_md:
            raise ValueError("skill_md is required")

        checked = LearnAuthoring.shared().lint_skill(
            name=name,
            skill_md=skill_md,
            files={str(k): str(v) for k, v in files.items()},
        )
        if not checked.get("compatible"):
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Skill failed lint — fix issues before saving.",
                    "lint": {
                        "compatible": False,
                        "errors": checked.get("errors") or [],
                        "warnings": checked.get("warnings") or [],
                    },
                },
                status=400,
            )

        root = Path(settings.SKILLS_DIR) / name
        root.mkdir(parents=True, exist_ok=True)
        (root / "SKILL.md").write_text(skill_md.rstrip() + "\n", encoding="utf-8")
        written = ["SKILL.md"]
        for rel, content in files.items():
            rel_s = str(rel).replace("\\", "/").lstrip("/")
            if not rel_s or ".." in rel_s.split("/"):
                raise ValueError(f"unsafe file path {rel!r}")
            path = root / rel_s
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(content).rstrip() + "\n", encoding="utf-8")
            written.append(rel_s)

        # Refresh registry so Catalog sees the new skill.
        try:
            SkillRegistry.shared().reload_skills()
        except Exception:  # noqa: BLE001
            pass

        return JsonResponse(
            {
                "ok": True,
                "path": str(root),
                "name": name,
                "files": written,
                "lint": {
                    "compatible": True,
                    "errors": [],
                    "warnings": checked.get("warnings") or [],
                },
            }
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)


urlpatterns = [
    path("", learn_page, name="learn"),
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
