"""Preset workflows: designer graph CRUD + the lab/project-create views.

One consolidated module (model-adjacent read/write helpers, the designer
page, and the project-creation picker API) — mirrors how ``catalog.py``
keeps every catalog view in one place instead of scattering them.
"""

from __future__ import annotations

import re

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import path
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from orchestrator.crew.roles.registry import RoleRegistry
from orchestrator.skills.registry import SkillRegistry
from orchestrator.tools.catalog import ToolCatalog
from orchestrator.workflow import (
    WorkflowLinter,
    auto_layout,
    compile_workflow,
    deduce_workflow_from_project,
    generate_workflow,
)
from peon.projects.http_helpers import json_errors, parse_json_body
from peon.projects.models import Project, WorkflowPreset

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def slugify_workflow_id(name: str) -> str:
    slug = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return slug or "workflow"


def _row(preset: WorkflowPreset) -> dict:
    return {
        "workflow_id": preset.workflow_id,
        "name": preset.name,
        "description": preset.description,
        "seed_inputs": preset.seed_inputs or [],
        "graph": preset.graph or {"nodes": [], "edges": []},
        "lint_errors": preset.lint_errors or {"errors": [], "warnings": []},
        "is_published": bool(preset.is_published),
        "updated_at": preset.updated_at.isoformat() if preset.updated_at else "",
    }


def list_workflows(*, published_only: bool = False) -> list[dict]:
    qs = WorkflowPreset.objects.all()
    if published_only:
        qs = qs.filter(is_published=True)
    return [_row(p) for p in qs]


def get_workflow(workflow_id: str) -> dict | None:
    preset = WorkflowPreset.objects.filter(pk=workflow_id).first()
    return _row(preset) if preset else None


def save_workflow(
    workflow_id: str,
    *,
    name: str,
    description: str = "",
    seed_inputs: list | None = None,
    graph: dict | None = None,
    is_published: bool = False,
) -> dict:
    """Validate + persist one preset. Publishing with lint errors is refused."""
    seed_inputs = seed_inputs if isinstance(seed_inputs, list) else []
    graph = graph if isinstance(graph, dict) else {"nodes": [], "edges": []}
    lint = WorkflowLinter.lint(graph=graph, seed_inputs=seed_inputs)
    if is_published and lint["errors"]:
        raise ValueError("cannot publish a workflow with lint errors: " + "; ".join(lint["errors"]))
    if (
        is_published
    ):  # publishing means "this will run": it must compile to a plan the scheduler accepts
        from peon.projects.planning import validate_project_objectives

        try:
            validate_project_objectives(
                compile_workflow(
                    graph=graph, seed_inputs=seed_inputs, name=name, description=description
                ).objectives
            )
        except Exception as exc:
            raise ValueError(
                f"cannot publish: the workflow does not compile to a runnable plan ({exc})"
            ) from exc
    preset, _ = WorkflowPreset.objects.update_or_create(
        workflow_id=workflow_id,
        defaults={
            "name": name.strip() or workflow_id,
            "description": description,
            "seed_inputs": seed_inputs,
            "graph": graph,
            "lint_errors": lint,
            "is_published": is_published,
        },
    )
    return _row(preset)


def delete_workflow(workflow_id: str) -> None:
    deleted, _ = WorkflowPreset.objects.filter(pk=workflow_id).delete()
    if not deleted:
        raise KeyError(f"unknown workflow {workflow_id!r}")


def designer_catalog() -> dict[str, list[dict]]:
    """Roles/skills/tools the designer palette can drop onto the canvas (with who may use what)."""
    skills_reg = SkillRegistry.shared()
    roles = [
        {
            "id": r.id,
            "label": r.id,
            "description": (r.goal or "")[:160],
            "role_kind": "manager"
            if r.is_manager
            else "analyzer"
            if r.is_analyzer
            else "specialist",
            "bound": list(skills_reg.skills_for_role(r.id))[:12],
        }
        for r in RoleRegistry.shared().list_roles()
    ]
    skills = [
        {
            "id": s.id,
            "label": s.id,
            "description": (s.description or "")[:160],
            "bound": list(s.role_ids or ()),
        }
        for s in skills_reg.list_skills()
    ]
    tools = [
        {"id": t["id"], "label": t["id"], "description": t["description"]}
        for t in ToolCatalog.shared().summaries(limit=200)
    ]
    return {"roles": roles, "skills": skills, "tools": tools}


@require_GET
def workflow_designer_page(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "lab/workflow_designer.html",
        {
            "nav": "lab",
            "lab_section": "workflows",
            "workflow_urls": {
                "catalog": "/lab/workflows/api/catalog/",
                "list": "/lab/workflows/api/",
                "create": "/lab/workflows/api/create/",
                "detail": "/lab/workflows/api/{id}/",
                "write": "/lab/workflows/api/{id}/",
                "delete": "/lab/workflows/api/{id}/",
                "lint": "/lab/workflows/api/lint/",
                "compile": "/lab/workflows/api/compile/",
                "generate": "/lab/workflows/api/generate/",
                "projects": "/lab/workflows/api/projects/",
                "fromProject": "/lab/workflows/api/from-project/",
            },
        },
    )


@require_GET
@json_errors
def api_workflow_catalog(_request: HttpRequest) -> JsonResponse:
    return JsonResponse({"ok": True, **designer_catalog()})


@require_GET
@json_errors
def api_workflow_list(request: HttpRequest) -> JsonResponse:
    published_only = (request.GET.get("published") or "").strip() == "1"
    return JsonResponse({"ok": True, "workflows": list_workflows(published_only=published_only)})


@require_http_methods(["GET", "POST", "DELETE"])
@json_errors
def api_workflow_detail(request: HttpRequest, workflow_id: str) -> JsonResponse:
    if request.method == "DELETE":
        delete_workflow(workflow_id)
        return JsonResponse({"ok": True, "workflow_id": workflow_id})
    if request.method == "POST":
        body = parse_json_body(request, strict=False) or {}
        row = save_workflow(
            workflow_id,
            name=str(body.get("name") or workflow_id),
            description=str(body.get("description") or ""),
            seed_inputs=body.get("seed_inputs") or [],
            graph=body.get("graph") or {},
            is_published=bool(body.get("is_published")),
        )
        return JsonResponse({"ok": True, "workflow": row})
    row = get_workflow(workflow_id)
    if row is None:
        return JsonResponse({"ok": False, "error": "not found"}, status=404)
    return JsonResponse({"ok": True, "workflow": row})


@require_POST
@json_errors
def api_workflow_create(request: HttpRequest) -> JsonResponse:
    body = parse_json_body(request, strict=False) or {}
    name = str(body.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    workflow_id = str(body.get("workflow_id") or "").strip() or slugify_workflow_id(name)
    row = save_workflow(
        workflow_id,
        name=name,
        description=str(body.get("description") or ""),
        seed_inputs=body.get("seed_inputs") or [],
        graph=body.get("graph") or {},
        is_published=False,
    )
    return JsonResponse({"ok": True, "workflow": row})


@require_POST
@json_errors
def api_workflow_lint(request: HttpRequest) -> JsonResponse:
    body = parse_json_body(request, strict=False) or {}
    lint = WorkflowLinter.lint(
        graph=body.get("graph") or {},
        seed_inputs=body.get("seed_inputs") or [],
    )
    return JsonResponse({"ok": True, **lint})


@require_POST
@json_errors
def api_workflow_compile(request: HttpRequest) -> JsonResponse:
    """Preview what a graph runs as: lint + the ordered objectives (with sample answers, if given)."""
    body = parse_json_body(request, strict=False) or {}
    graph, seeds = body.get("graph") or {}, body.get("seed_inputs") or []
    lint = WorkflowLinter.lint(graph=graph, seed_inputs=seeds)
    plan = None
    if not lint["errors"]:
        compiled = compile_workflow(
            graph=graph,
            seed_inputs=seeds,
            answers=body.get("answers") or {},
            name=str(body.get("name") or ""),
            description=str(body.get("description") or ""),
        )
        plan = {"objectives": compiled.preview(), "role_ids": compiled.role_ids}
    return JsonResponse({"ok": True, **lint, "plan": plan})


@require_POST
@json_errors
def api_workflow_generate(request: HttpRequest) -> JsonResponse:
    """Draft a workflow from a goal (LLM + catalog + linter); the designer loads the result as a draft."""
    body = parse_json_body(request, strict=False) or {}
    return JsonResponse({"ok": True, "workflow": generate_workflow(str(body.get("goal") or ""))})


@require_GET
@json_errors
def api_workflow_projects(_request: HttpRequest) -> JsonResponse:
    """Projects the designer can reverse into a workflow draft."""
    rows = [
        {
            "id": str(p.id),
            "title": p.title,
            "summary": (p.summary or "")[:160],
            "status": p.status,
        }
        for p in Project.objects.order_by("-updated_at")[:100]
    ]
    return JsonResponse({"ok": True, "projects": rows})


@require_POST
@json_errors
def api_workflow_from_project(request: HttpRequest) -> JsonResponse:
    """Deduce a workflow draft from an existing project's brief and objectives."""
    body = parse_json_body(request, strict=False) or {}
    project_id = str(body.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("project_id is required")
    return JsonResponse({"ok": True, "workflow": deduce_workflow_from_project(project_id)})


@require_POST
@json_errors
def api_workflow_layout(request: HttpRequest) -> JsonResponse:
    body = parse_json_body(request, strict=False) or {}
    return JsonResponse({"ok": True, "graph": auto_layout(body.get("graph") or {})})


def compile_preset(preset: dict, answers: dict[str, str]):
    """The plan a preset (published or draft) runs as for one set of seed answers (raises ``WorkflowCompileError``)."""
    return compile_workflow(
        graph=preset["graph"],
        seed_inputs=preset["seed_inputs"],
        answers=answers,
        name=preset["name"],
        description=preset["description"],
    )


urlpatterns = [
    path("", workflow_designer_page, name="lab_workflows"),
    path("api/catalog/", api_workflow_catalog, name="lab_workflows_api_catalog"),
    path("api/", api_workflow_list, name="lab_workflows_api_list"),
    path("api/create/", api_workflow_create, name="lab_workflows_api_create"),
    path("api/lint/", api_workflow_lint, name="lab_workflows_api_lint"),
    path("api/compile/", api_workflow_compile, name="lab_workflows_api_compile"),
    path("api/generate/", api_workflow_generate, name="lab_workflows_api_generate"),
    path("api/projects/", api_workflow_projects, name="lab_workflows_api_projects"),
    path("api/from-project/", api_workflow_from_project, name="lab_workflows_api_from_project"),
    path("api/layout/", api_workflow_layout, name="lab_workflows_api_layout"),
    path("api/<str:workflow_id>/", api_workflow_detail, name="lab_workflows_api_detail"),
]
