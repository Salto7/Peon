"""Operator UI pages: home, settings, project list/create/detail, reports."""

from __future__ import annotations


from django.contrib import messages
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from peon.projects.findings import FindingStore
from peon.projects.asset_graph import AssetGraphEngine
from peon.projects.http_helpers import (
    scoped_get_or_404,
    with_project_context,
    wants_json as request_wants_json,
)
from orchestrator.utils.llm import llm_configured
from peon.projects.models import (
    FindingSeverity,
    FindingStatus,
    Job,
    JobStatus,
    Project,
    ProjectKind,
    ProjectStatus,
    StreamMessage,
    StreamMessageType,
)
from peon.projects.plan_graph import plan_graph_payload
from peon.projects.objectives import ObjectiveScheduler
from peon.projects.sandbox import ProjectSandbox
from peon.projects.planning import create_project_shell, run_llm_plan
from peon.projects.streaming import project_message_dicts
from peon.projects.roe_ops import roe_add_path
from peon.projects.target_shapes import (
    format_targets,
    parse_target_lines,
)
from peon.projects.workspaces import (
    list_project_inputs,
    list_reports,
    resolve_report_path,
    save_project_input,
)
from peon.projects.project_status import ProjectOpsPayload
from peon.projects.catalog_cards.role import RoleCards
from peon.projects.runtime_settings import PeonSettings
from peon.projects.llm_proxy import LlmProxy
from peon.projects.crew_control import set_crew_status
from peon.projects.role_job_tree import RoleJobTree

PROJECT_LIST_LIMIT = 50
PROJECT_JOBS_LIMIT = 40
STREAM_BOOTSTRAP_LIMIT = 120
STREAM_POLL_LIMIT = 200
HOME_PROJECT_LIMIT = 40
HOME_ALERT_LIMIT = 12
ALERTS_CLEARED_KEY = "alerts_cleared_at"


def _alerts_cleared_at(request: HttpRequest):
    """When the operator last cleared the home alerts (None = never)."""
    from django.utils.dateparse import parse_datetime

    raw = request.session.get(ALERTS_CLEARED_KEY)
    return parse_datetime(raw) if raw else None


@require_http_methods(["POST"])
def alerts_clear(request: HttpRequest) -> HttpResponse:
    """Dismiss every alert currently shown on the home page."""
    from django.utils import timezone

    request.session[ALERTS_CLEARED_KEY] = timezone.now().isoformat()
    messages.success(request, "Alerts cleared")
    return redirect("home")


@require_GET
def home(request: HttpRequest) -> HttpResponse:
    """Control-plane dashboard: KPIs, project table, recent errors."""
    projects = list(Project.objects.all()[:HOME_PROJECT_LIMIT])
    sandbox = ProjectSandbox.shared()
    per_project = sandbox.per_project()
    project_rows = [
        {
            "project": p,
            "sandbox_name": ProjectSandbox.container_name(str(p.pk)),
            "sandbox_mode": (
                "openshell"
                if getattr(p, "sandbox_runtime", "") == "openshell"
                else ("dedicated" if per_project else "shared")
            ),
        }
        for p in projects
    ]
    from django.db.models import Sum

    from peon.projects.db import iter_project_aliases

    active_n = Project.objects.filter(status=ProjectStatus.ACTIVE).count()
    paused_n = Project.objects.filter(status=ProjectStatus.PAUSED).count()
    agg = Project.objects.aggregate(
        running=Sum("running_jobs"), failed=Sum("failed_jobs")
    )
    running_jobs = int(agg.get("running") or 0)
    failed_jobs = int(agg.get("failed") or 0)
    failed_recent = []
    stream_errors = []
    cleared_at = _alerts_cleared_at(request)
    for pid, alias in iter_project_aliases():
        if len(failed_recent) < HOME_ALERT_LIMIT:
            failed_qs = (
                Job.objects.using(alias)
                .filter(status=JobStatus.FAILED)
                .exclude(error="")
            )
            if cleared_at:
                failed_qs = failed_qs.filter(updated_at__gt=cleared_at)
            for job in failed_qs.order_by("-updated_at")[
                : HOME_ALERT_LIMIT - len(failed_recent)
            ]:
                job.project = Project.objects.filter(pk=pid).first()
                failed_recent.append(job)
        if len(stream_errors) < HOME_ALERT_LIMIT:
            err_qs = StreamMessage.objects.using(alias).filter(
                message_type=StreamMessageType.ERROR
            )
            if cleared_at:
                err_qs = err_qs.filter(created_at__gt=cleared_at)
            for msg in err_qs.select_related("job").order_by("-created_at")[
                : HOME_ALERT_LIMIT - len(stream_errors)
            ]:
                stream_errors.append(msg)
    notices: list[str] = []
    if not llm_configured():
        notices.append("LLM API key is not configured — planning and Toolsmith are limited.")
    return render(
        request,
        "home.html",
        {
            "nav": "home",
            "kpis": {
                "active_projects": active_n,
                "paused_projects": paused_n,
                "running_jobs": running_jobs,
                "failed_jobs": failed_jobs,
            },
            "project_rows": project_rows,
            "failed_recent": failed_recent,
            "stream_errors": stream_errors,
            "notices": notices,
            "sandbox_per_project": per_project,
        },
    )


@require_http_methods(["GET", "POST"])
def settings_page(request: HttpRequest) -> HttpResponse:
    """Operator runtime policy (caps + agent governors)."""
    restart_needed = False
    if request.method == "POST":

        updates = {}
        for field in PeonSettings.field_meta():
            key = field["key"]
            if key not in request.POST:
                continue
            updates[key] = request.POST.get(key)
        prev_proxy = PeonSettings.get_bool("LLM_PROXY_ENABLED", False)
        try:
            _values, restart_changed = PeonSettings.update(updates)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("settings_page")
        new_proxy = PeonSettings.get_bool("LLM_PROXY_ENABLED", False)
        if restart_changed:
            restart_needed = True
            keys = ", ".join(restart_changed[:8])
            more = f" (+{len(restart_changed) - 8} more)" if len(restart_changed) > 8 else ""
            messages.warning(
                request,
                f"Saved. Restart Peon for these settings to apply: {keys}{more}. "
                "Typically: docker compose restart web worker "
                "(recreate cognee/edge if you changed their ports or compose env).",
            )
        else:
            messages.success(request, "Settings saved.")
        if prev_proxy != new_proxy:
            st = LlmProxy.shared().status()
            if new_proxy and st.get("running"):
                messages.info(
                    request,
                    "LiteLLM proxy is running — OpenCode Toolsmith authoring is available.",
                )
            elif new_proxy and not st.get("running"):
                messages.warning(
                    request,
                    "LiteLLM proxy enabled but the container is not running. "
                    f"Check docker for {st.get('name') or 'peon-litellm'}"
                    + (f": {st.get('error')}" if st.get("error") else "."),
                )
            else:
                messages.info(request, "LiteLLM proxy stopped.")
        return redirect("settings_page")
    return render(
        request,
        "projects/settings.html",
        {
            "fields": PeonSettings.field_meta(),
            "categories": PeonSettings.categorized_fields(),
            "restart_needed": restart_needed,
            "nav": "settings",
        },
    )


@require_http_methods(["GET", "POST"])
def project_list(request: HttpRequest) -> HttpResponse:
    projects = Project.objects.all()[:PROJECT_LIST_LIMIT]
    return render(
        request,
        "projects/list.html",
        {
            "projects": projects,
            "nav": "projects",
        },
    )


@require_http_methods(["GET", "POST"])
def project_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        wants_json = request_wants_json(request)
        result = _create_project_from_post(request)
        if wants_json:
            status = 200 if result.get("ok") else 400
            return JsonResponse(result, status=status)
        for kind, text in result.get("flashes") or []:
            getattr(messages, kind, messages.info)(request, text)
        if result.get("project_id"):
            return redirect("project_detail", pk=result["project_id"])
        return redirect("project_create")

    return render(
        request,
        "projects/create.html",
        {
            "nav": "projects",
            "available_roles": RoleCards.picker(),
            "selected_roles": RoleCards.required_names(),
        },
    )


def _create_project_from_post(request: HttpRequest) -> dict:
    """Create project + RoE + optional LLM plan. Used by HTML and AJAX create."""
    stages: list[dict] = []

    def stage(key: str, label: str, status: str = "done", detail: str = "") -> None:
        stages.append(
            {"key": key, "label": label, "status": status, "detail": detail or ""}
        )

    title = (request.POST.get("title") or "").strip() or "untitled"
    summary = (request.POST.get("summary") or "").strip()
    operator_scope = bool((request.POST.get("in_scope") or "").strip())
    in_scope = parse_target_lines(request.POST.get("in_scope") or "")
    sandbox_runtime = (request.POST.get("sandbox_runtime") or "").strip()
    kind = ProjectKind.resolve(request.POST.get("kind") or "")
    flashes: list[tuple[str, str]] = []

    # Uploads need a project id — create shell first without paths, then attach.
    project, scope, roe = create_project_shell(
        title=title,
        summary=summary,
        in_scope=in_scope,
        path_values=[],
        operator_supplied_scope=operator_scope,
        sandbox_runtime=sandbox_runtime,
        kind=kind,
    )
    stage("create", "Created project", "done", project.title)

    uploaded_paths: list[str] = []
    for f in request.FILES.getlist("uploads"):
        try:
            saved = save_project_input(str(project.id), f)
            uploaded_paths.append(saved["sandbox_path"])
            roe_add_path(project, saved["sandbox_path"])
        except Exception as exc:
            flashes.append(
                ("warning", f"Upload skipped ({getattr(f, 'name', '?')}): {exc}")
            )
    if uploaded_paths:
        project.refresh_from_db()
        roe = getattr(project, "roe", None)
        scope = list(roe.in_scope) if roe else scope

    scope_detail = ", ".join(format_targets(scope)[:8]) if scope else "none yet"
    stage("roe", "Configured Rules of Engagement", "done", scope_detail)

    if in_scope and not operator_scope and not uploaded_paths:
        flashes.append(("info", "Rules of Engagement deduced: " + ", ".join(format_targets(in_scope))))
    if uploaded_paths:
        flashes.append(
            (
                "info",
                "Stored "
                + str(len(uploaded_paths))
                + " file(s) under project inputs (sandbox paths in Rules of Engagement).",
            )
        )

    picked = [
        str(s).strip()
        for s in request.POST.getlist("role_ids")
        if str(s).strip()
    ]
    required = RoleCards.required_names()
    picked = list(dict.fromkeys([*required, *picked]))
    brief = (summary or title).strip()
    if uploaded_paths:
        brief = (
            brief
            + "\n\nOperator uploaded sample(s) (project-local only):\n"
            + "\n".join(f"- {p}" for p in uploaded_paths)
        ).strip()

    redirect_url = reverse("project_detail", kwargs={"pk": project.pk})
    out: dict = {
        "ok": True,
        "project_id": str(project.id),
        "title": project.title,
        "redirect": redirect_url,
        "objectives": 0,
        "job_title": "",
        "plan_ok": False,
        "stages": stages,
        "flashes": flashes,
        "message": f"Created project {project.title}",
    }

    if llm_configured() and brief:
        stage("plan", "Planning objectives…", "active")
        try:
            result = run_llm_plan(
                description=brief,
                mode="project",
                title=project.title,
                summary=project.summary,
                in_scope=scope,
                exclusions=list(roe.exclusions) if roe else [],
                authorization=(roe.authorization_note if roe else ""),
                role_ids=picked or None,
                preferred_tags=list(project.focus_tags or []),
                project=project,
            )
            n_obj = len(result.objectives or [])
            job_title = result.job.title if result.job else "no ready objective"
            stages[-1] = {
                "key": "plan",
                "label": "Planned objectives",
                "status": "done",
                "detail": f"{n_obj} objective(s)",
            }
            if project.kind == ProjectKind.DURABLE:
                stage(
                    "start",
                    "Review durable plan",
                    "done",
                    "accept agent graph before start",
                )
                redirect_url = reverse("project_plan", kwargs={"pk": project.pk})
                msg = (
                    f"Created durable project {project.title} — "
                    f"review {n_obj} planned agent(s)"
                )
                flashes.append(("success", msg))
                out.update(
                    {
                        "objectives": n_obj,
                        "job_title": "",
                        "plan_ok": True,
                        "message": msg,
                        "redirect": redirect_url,
                        "kind": project.kind,
                        "needs_plan_review": True,
                    }
                )
            else:
                stage(
                    "start",
                    "Queued first agent",
                    "done",
                    job_title if result.job else "waiting for a ready objective",
                )
                msg = (
                    f"Created {project.title} — queued {job_title} ({n_obj} objectives)"
                )
                flashes.append(("success", msg))
                out.update(
                    {
                        "objectives": n_obj,
                        "job_title": job_title if result.job else "",
                        "plan_ok": True,
                        "message": msg,
                        "kind": project.kind,
                    }
                )
        except Exception as exc:
            stages[-1] = {
                "key": "plan",
                "label": "Planning failed",
                "status": "error",
                "detail": str(exc)[:240],
            }
            stage("start", "Open war room to retry", "done", "plan can be retried from chat")
            msg = f"Created {project.title} but plan failed: {exc}"

            set_crew_status(project, "awaiting_feedback")
            flashes.append(("error", msg))
            # Project exists — keep ok=True so the UI redirects; plan can retry from chat.
            out.update(
                {
                    "ok": True,
                    "message": msg,
                    "error": str(exc)[:500],
                    "plan_ok": False,
                }
            )
    else:
        if not llm_configured():
            stage(
                "plan",
                "Skipped auto-plan",
                "done",
                "Set OPENROUTER_API_KEY to auto-plan",
            )
            flashes.append(
                (
                    "warning",
                    f"Created {project.title} — set OPENROUTER_API_KEY to auto-plan",
                )
            )
            out["message"] = flashes[-1][1]
        else:
            stage("plan", "Skipped auto-plan", "done", "empty brief")
            flashes.append(("success", f"Created project {project.title}"))
        stage("start", "Opening war room", "done")

    out["stages"] = stages
    out["flashes"] = flashes
    out.setdefault("kind", project.kind)
    # Durable always lands on the plan graph (even when auto-plan was skipped).
    if project.kind == ProjectKind.DURABLE:
        plan_url = reverse("project_plan", kwargs={"pk": project.pk})
        out["redirect"] = plan_url
        out["needs_plan_review"] = True
    return out


@require_GET
@with_project_context
def project_plan(request: HttpRequest, pk) -> HttpResponse:
    """Durable plan review: editable agent graph before start."""
    project = get_object_or_404(Project, pk=pk)
    if project.kind != ProjectKind.DURABLE:
        return redirect("project_detail", pk=project.pk)
    payload = plan_graph_payload(project)
    return render(
        request,
        "projects/plan.html",
        {
            "nav": "projects",
            "project": project,
            "plan_graph": payload,
            "available_roles": RoleCards.picker(),
        },
    )


@require_GET
@with_project_context
def project_detail(request: HttpRequest, pk) -> HttpResponse:
    project = get_object_or_404(Project, pk=pk)
    if project.kind == ProjectKind.DURABLE and not project.plan_accepted:
        return redirect("project_plan", pk=project.pk)
    ops = ProjectOpsPayload.for_project(project, jobs_limit=PROJECT_JOBS_LIMIT)
    objectives = list(project.objectives.all())
    roe = getattr(project, "roe", None)
    bootstrap = project_message_dicts(
        project, limit=STREAM_BOOTSTRAP_LIMIT, newest_first=True
    )
    reports = list_reports(str(project.id))
    board = FindingStore.board_query(project, request, default_status="open")
    status_f = board["status"]
    severity_f = board["severity"]
    findings = board["findings"]
    finding_counts = board["counts"]
    has_next_objective = ObjectiveScheduler().next_ready(project) is not None
    project_inputs = list_project_inputs(str(project.id))
    # Full finding set for the asset graph (board may be status-filtered).
    graph_findings = list(project.findings.order_by("seq", "created_at")[:300])
    attack_surface = AssetGraphEngine(project).engagement(
        findings=graph_findings,
        rebuild=True,
    )
    try:
        org_chart = RoleJobTree.org_nodes_for_project(project)
    except Exception:
        org_chart = {"roots": [], "roles": [], "crew_status": "", "crew_flow_id": ""}
    try:
        from peon.projects.mcp_ops import list_project_mcp_configs

        mcp_configs = list_project_mcp_configs(project.pk)
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "project mcp configs failed for %s", project.pk, exc_info=True
        )
        mcp_configs = []
    console_ctx = {
        "pk": str(project.pk),
        "title": project.title,
        "kind": project.kind,
        "summary": project.summary or "",
        "runtime": project.get_sandbox_runtime_display(),
        "planUrl": (
            reverse("project_plan", kwargs={"pk": project.pk})
            if project.kind == ProjectKind.DURABLE
            else ""
        ),
        "findingStatuses": FindingStatus.choices,
        "findingSeverities": FindingSeverity.choices,
    }
    return render(
        request,
        "projects/detail.html",
        {
            "console_ctx": console_ctx,
            "project": project,
            "roe": roe,
            "objectives": objectives,
            "ops": ops,
            "reports": reports,
            "findings": findings,
            "finding_status": status_f,
            "finding_severity": severity_f,
            "finding_counts": finding_counts,
            "finding_statuses": FindingStatus.choices,
            "finding_severities": FindingSeverity.choices,
            "has_next_objective": has_next_objective,
            "project_inputs": project_inputs,
            "attack_surface": attack_surface,
            "org_chart": org_chart,
            "mcp_configs": mcp_configs,
            "nav": "projects",
            "stream_bootstrap": bootstrap,
        },
    )


@require_GET
@with_project_context
def job_live(request: HttpRequest, pk, job_id) -> HttpResponse:
    project = get_object_or_404(Project, pk=pk)
    job = scoped_get_or_404(Job, project, pk=job_id)
    return render(
        request,
        "projects/job_live.html",
        {"project": project, "job": job, "nav": "projects"},
    )


@require_GET
@with_project_context
def project_report_view(request: HttpRequest, pk, file_path: str) -> HttpResponse:
    """In-browser report viewer (Markdown rendered server-side; other text as pre)."""
    project = get_object_or_404(Project, pk=pk)
    path = resolve_report_path(str(project.id), file_path)
    if path is None:
        raise Http404("Report not found")
    from peon.projects.workspaces import is_markdown_path, render_markdown_html

    text = path.read_text(encoding="utf-8", errors="replace")
    report_html = render_markdown_html(text) if is_markdown_path(path) else ""
    return render(
        request,
        "projects/report_view.html",
        {
            "project": project,
            "report_name": path.name,
            "report_path": file_path,
            "report_text": text,
            "report_html": report_html,
            "is_markdown": bool(report_html),
            "download_url": reverse(
                "project_report_file", kwargs={"pk": project.id, "file_path": file_path}
            ),
            "nav": "projects",
        },
    )


@require_GET
@with_project_context
def project_report_file(request: HttpRequest, pk, file_path: str) -> HttpResponse:
    """Download (or inline view) a report file from the project workspace."""
    project = get_object_or_404(Project, pk=pk)
    path = resolve_report_path(str(project.id), file_path)
    if path is None:
        raise Http404("Report not found")
    inline = (request.GET.get("view") or "").strip() in {"1", "true", "yes"}
    if path.suffix.lower() in {".html", ".htm"}:
        inline = False
    disposition = "inline" if inline else "attachment"
    response = FileResponse(path.open("rb"), as_attachment=not inline, filename=path.name)
    response["Content-Disposition"] = f'{disposition}; filename="{path.name}"'
    return response

