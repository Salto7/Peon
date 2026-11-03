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
from orchestrator.toolsmith import PeonAuthoring
from orchestrator.utils.llm import LLM_NOT_CONFIGURED, llm_configured
from orchestrator.utils.strings import is_kebab
from peon.projects.catalog_cards.tool import ToolCards
from peon.projects.http_helpers import json_errors, parse_json_body, split_csv, sse_response
from peon.projects.lab_service import LabService
from peon.projects.llm_proxy import LlmProxy
from peon.projects.opencode import PROXY_REQUIRED_MSG, OpenCodeEngine
from peon.projects.terminal_session import LAB_SCOPE, decode_input_bytes, get_registry

ENGINES = {"peon": PeonAuthoring, "opencode": OpenCodeEngine}


def _engine(body: dict):
    """Toolsmith's authoring backend for one request. ``peon`` is the default —

    plain ``chat_json``, no lab container needed. ``opencode`` additionally
    requires the LiteLLM proxy and actually runs its draft in the lab.
    """
    name = str(body.get("engine") or "peon").strip().lower()
    return ENGINES.get(name, PeonAuthoring)


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


def _authoring_gate(engine: type) -> JsonResponse | None:
    if not llm_configured():
        return JsonResponse({"ok": False, "error": LLM_NOT_CONFIGURED}, status=503)
    if engine is OpenCodeEngine:
        return _proxy_gate()
    return None


def _proxy_gate() -> JsonResponse | None:
    """Block OpenCode specifically when LiteLLM proxy intent is off (the Peon
    engine talks to Peon's own LLM plumbing directly and never needs it)."""
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
def toolsmith_page(request: HttpRequest) -> HttpResponse:
    proxy = LlmProxy.shared().snapshot()
    return render(
        request,
        "toolsmith/index.html",
        {
            "nav": "lab",
            "lab_section": "toolsmith",
            "llm_ready": llm_configured(),
            "llm_proxy_enabled": proxy["enabled"],
            "llm_proxy_running": proxy["running"],
            "llm_proxy_url": proxy["url"],
            # Peon engine only needs an LLM; OpenCode also needs the proxy
            # (it talks to Peon's LLM through LiteLLM, from inside the lab).
            "authoring_ready": llm_configured(),
            "opencode_ready": bool(llm_configured() and proxy["enabled"]),
            "tools": ToolCards.summaries(),
            "lab": LabService.shared().status(),
        },
    )


@require_http_methods(["POST"])
@json_errors
def toolsmith_suggest_tool(request: HttpRequest) -> JsonResponse:
    body = _parse_body(request)
    engine = _engine(body)
    gated = _authoring_gate(engine)
    if gated is not None:
        return gated
    result = engine.shared().suggest_tool(str(body.get("prompt") or ""))
    return JsonResponse(
        {
            "ok": True,
            "mode": "tool",
            "id": result["id"],
            "yaml": result["yaml"],
            "install_script": result.get("install_script") or "",
            "notes": result.get("notes") or "",
            "author": "opencode" if engine is OpenCodeEngine else "peon",
        }
    )


@require_http_methods(["POST"])
@json_errors
def toolsmith_write_skill(request: HttpRequest) -> JsonResponse:
    body = _parse_body(request)
    engine = _engine(body)
    gated = _authoring_gate(engine)
    if gated is not None:
        return gated
    tools = body.get("tools") or []
    if isinstance(tools, str):
        tools = split_csv(tools)
    result = engine.shared().write_skill(str(body.get("prompt") or ""), tools=list(tools))
    result["author"] = "opencode" if engine is OpenCodeEngine else "peon"
    return JsonResponse({"ok": True, "mode": "skill", **result})


@require_http_methods(["POST"])
@json_errors
def toolsmith_save_tool(request: HttpRequest) -> JsonResponse:
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


def _test_result_response(result: dict) -> dict:
    """Shape one ``test_tool_install``-style result dict for the frontend —
    shared by the inline-fallback start response and the status poll."""
    return {
        "ok": True,
        "install_ok": bool(result.get("ok")),
        "verified": bool(result.get("verified")),
        "message": result.get("message") or "",
        "log": result.get("log") or "",
        "verify_output": result.get("verify_output") or "",
        "verify_steps": result.get("verify_steps") or [],
        "lab": result.get("lab") or LabService.shared().status(),
        "tool_id": result.get("tool_id") or "",
    }


@require_http_methods(["POST"])
@json_errors
def toolsmith_test_tool(request: HttpRequest) -> JsonResponse:
    """Start an install test on the worker (same web-side docker-rm network
    flap rationale as the lab lifecycle actions — ``docker rm``/``docker
    run`` for the lab container inside this view's own request can drop the
    browser's connection to it). Answers immediately with a run id; the
    frontend polls ``toolsmith_test_tool_status`` for the actual result.
    """
    from peon.projects.tasks import enqueue_tool_test

    body = _parse_body(request)
    outcome = enqueue_tool_test(
        yaml_text=str(body.get("yaml") or ""),
        install_script=str(body.get("install_script") or ""),
    )
    if not outcome.get("ok"):
        status = 409 if _LAB_BUSY in str(outcome.get("error") or "") else 500
        return JsonResponse(outcome, status=status)
    return JsonResponse(outcome)


@require_GET
def toolsmith_test_tool_status(request: HttpRequest, run_id: str) -> JsonResponse:
    from peon.projects.models import ToolTestRun

    run = ToolTestRun.objects.filter(pk=run_id).first()
    if run is None:
        return JsonResponse({"ok": False, "error": "unknown test run"}, status=404)
    if run.status != "done":
        return JsonResponse({"ok": True, "status": "running"})
    return JsonResponse({**_test_result_response(run.result or {}), "status": "done"})


@require_http_methods(["POST"])
@json_errors
def toolsmith_replan_tool(request: HttpRequest) -> JsonResponse:
    body = _parse_body(request)
    engine = _engine(body)
    gated = _authoring_gate(engine)
    if gated is not None:
        return gated
    result = engine.shared().replan_tool(
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
            "author": "opencode" if engine is OpenCodeEngine else "peon",
        }
    )


@require_GET
def toolsmith_lab_status(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"ok": True, "lab": LabService.shared().status()})


_LAB_BUSY = "is busy with another"


def _lab_response(action) -> JsonResponse:
    """Run one lab lifecycle action; always answer with the lab's own status.

    A "lab busy" mutex rejection — raised directly (``setup``/``test``, which
    run inline) or returned as ``{"ok": False, ...}`` (``enqueue_lab_*``'s own
    pre-enqueue probe) — answers 409 so the frontend can reliably offer a
    force-retry instead of just showing a generic error.
    """
    try:
        result = action()
    except Exception as exc:  # noqa: BLE001
        status = 409 if _LAB_BUSY in str(exc) else 500
        return JsonResponse(
            {"ok": False, "error": str(exc), "lab": LabService.shared().status()},
            status=status,
        )
    if "lab" not in result:
        result["lab"] = LabService.shared().status()
    status = 409 if not result.get("ok") and _LAB_BUSY in str(result.get("error") or "") else 200
    return JsonResponse(result, status=status)


def _force_flag(request: HttpRequest) -> bool:
    body = _parse_body(request)
    raw = body.get("force")
    if raw is None:
        raw = request.POST.get("force")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


@require_http_methods(["POST"])
def toolsmith_lab_setup(request: HttpRequest) -> JsonResponse:
    """Ensure the lab is running — reused as-is, never wiped."""
    force = _force_flag(request)
    return _lab_response(lambda: LabService.shared().setup(force=force))


@require_http_methods(["POST"])
def toolsmith_lab_reboot(request: HttpRequest) -> JsonResponse:
    """Queue a restart in place (no wipe) on the worker — unstick a hung lab."""
    from peon.projects.tasks import enqueue_lab_reboot

    force = _force_flag(request)
    return _lab_response(lambda: enqueue_lab_reboot(force=force))


@require_http_methods(["POST"])
def toolsmith_lab_decommission(request: HttpRequest) -> JsonResponse:
    """Queue lab removal on the worker (avoids a web-side docker-rm network flap)."""
    from peon.projects.tasks import enqueue_lab_decommission

    force = _force_flag(request)
    return _lab_response(lambda: enqueue_lab_decommission(force=force))


@require_http_methods(["POST"])
def toolsmith_lab_reset(request: HttpRequest) -> JsonResponse:
    """Queue a decommission + fresh setup on the worker — guarantees one clean lab."""
    from peon.projects.tasks import enqueue_lab_reset

    force = _force_flag(request)
    return _lab_response(lambda: enqueue_lab_reset(force=force))


def _lab_session_or_404(session_id: str):
    sess = get_registry().get(session_id, LAB_SCOPE)
    if sess is None:
        return None, JsonResponse({"ok": False, "error": "Session not found"}, status=404)
    return sess, None


@require_http_methods(["POST"])
def toolsmith_lab_terminal_open(request: HttpRequest) -> JsonResponse:
    """Open (or reuse) the one interactive shell into the running lab container."""
    body = parse_json_body(request)
    try:
        cols = int(body.get("cols") or request.POST.get("cols") or 80)
        rows = int(body.get("rows") or request.POST.get("rows") or 24)
    except (TypeError, ValueError):
        cols, rows = 80, 24
    try:
        sess, reused = get_registry().open_lab(cols=cols, rows=rows)
    except RuntimeError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=503)
    return JsonResponse(
        {
            "ok": True,
            "session_id": sess.id,
            "container": sess.container,
            "cols": sess.cols,
            "rows": sess.rows,
            "reused": reused,
        }
    )


@require_GET
def toolsmith_lab_terminal_stream(request: HttpRequest, session_id: str) -> HttpResponse:
    sess, err = _lab_session_or_404(session_id)
    if err is not None:
        return err
    return sse_response(request, get_registry().iter_sse_events(sess))


@require_http_methods(["POST"])
def toolsmith_lab_terminal_input(request: HttpRequest, session_id: str) -> JsonResponse:
    sess, err = _lab_session_or_404(session_id)
    if err is not None:
        return err
    body = parse_json_body(request)
    raw = body.get("data")
    if raw is None:
        raw = request.POST.get("data") or ""
    try:
        data = decode_input_bytes(raw, base64_encoded=body.get("encoding") == "base64")
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid input"}, status=400)
    try:
        sess.write(data)
    except OSError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=410)
    return JsonResponse({"ok": True})


@require_http_methods(["POST"])
def toolsmith_lab_terminal_resize(request: HttpRequest, session_id: str) -> JsonResponse:
    sess, err = _lab_session_or_404(session_id)
    if err is not None:
        return err
    body = parse_json_body(request)
    try:
        cols = int(body.get("cols") or request.POST.get("cols") or sess.cols)
        rows = int(body.get("rows") or request.POST.get("rows") or sess.rows)
    except (TypeError, ValueError):
        cols, rows = sess.cols, sess.rows
    sess.resize(cols, rows)
    return JsonResponse({"ok": True, "cols": sess.cols, "rows": sess.rows})


@require_http_methods(["POST"])
def toolsmith_lab_terminal_close(request: HttpRequest, session_id: str) -> JsonResponse:
    ok = get_registry().close(session_id, LAB_SCOPE)
    return JsonResponse({"ok": True, "closed": ok})


@require_http_methods(["POST"])
@json_errors
def toolsmith_lint_skill(request: HttpRequest) -> JsonResponse:
    body = _parse_body(request)
    name = str(body.get("name") or "").strip()
    file_map_in = _file_map(body.get("files") or {})
    from orchestrator.skills.author import prepare_claude_skill
    from orchestrator.toolsmith.draft import draft_binary

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
def toolsmith_save_skill(request: HttpRequest) -> JsonResponse:
    from orchestrator.skills.author import save_claude_skill

    body = _parse_body(request)
    name = _safe_slug(str(body.get("name") or ""), kind="skill name")
    skill_md = str(body.get("skill_md") or "").strip()
    if not skill_md:
        raise ValueError("skill_md is required")
    from orchestrator.toolsmith.draft import draft_binary

    saved = save_claude_skill(
        name=name,
        skill_md=skill_md,
        files=_file_map(body.get("files") or {}),
        binary=draft_binary(skill_md),
    )
    status = 200 if saved.get("ok") else 400
    return JsonResponse(saved, status=status)


urlpatterns = [
    path("", toolsmith_page, name="lab_toolsmith"),
    path("api/tool/", toolsmith_suggest_tool, name="toolsmith_api_tool"),
    path("api/tool/save/", toolsmith_save_tool, name="toolsmith_api_tool_save"),
    path("api/tool/test/", toolsmith_test_tool, name="toolsmith_api_tool_test"),
    path(
        "api/tool/test/<str:run_id>/",
        toolsmith_test_tool_status,
        name="toolsmith_api_tool_test_status",
    ),
    path("api/tool/replan/", toolsmith_replan_tool, name="toolsmith_api_tool_replan"),
    path("api/skill/", toolsmith_write_skill, name="toolsmith_api_skill"),
    path("api/skill/lint/", toolsmith_lint_skill, name="toolsmith_api_skill_lint"),
    path("api/skill/save/", toolsmith_save_skill, name="toolsmith_api_skill_save"),
    path("api/lab/", toolsmith_lab_status, name="toolsmith_api_lab"),
    path("api/lab/setup/", toolsmith_lab_setup, name="toolsmith_api_lab_setup"),
    path("api/lab/reboot/", toolsmith_lab_reboot, name="toolsmith_api_lab_reboot"),
    path("api/lab/decommission/", toolsmith_lab_decommission, name="toolsmith_api_lab_decommission"),
    path("api/lab/reset/", toolsmith_lab_reset, name="toolsmith_api_lab_reset"),
    path("api/lab/terminal/", toolsmith_lab_terminal_open, name="toolsmith_api_lab_terminal_open"),
    path(
        "api/lab/terminal/<str:session_id>/stream/",
        toolsmith_lab_terminal_stream,
        name="toolsmith_api_lab_terminal_stream",
    ),
    path(
        "api/lab/terminal/<str:session_id>/input/",
        toolsmith_lab_terminal_input,
        name="toolsmith_api_lab_terminal_input",
    ),
    path(
        "api/lab/terminal/<str:session_id>/resize/",
        toolsmith_lab_terminal_resize,
        name="toolsmith_api_lab_terminal_resize",
    ),
    path(
        "api/lab/terminal/<str:session_id>/close/",
        toolsmith_lab_terminal_close,
        name="toolsmith_api_lab_terminal_close",
    ),
]
