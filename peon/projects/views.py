"""Operator UI: pages, control POSTs, JSON polls, report download."""

from __future__ import annotations

import json
from pathlib import Path

from django.contrib import messages
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from peon.projects.findings import FindingStore
from peon.projects.asset_graph import engagement_graph
from peon.projects.http_helpers import (
    request_value,
    wants_json as request_wants_json,
)
from peon.projects.jobs import anchor_job
from peon.projects.streaming import emit_job_stream, project_message_dicts, stream_meta
from peon.projects.lifecycle import ProjectLifecycle
from orchestrator.utils.llm import llm_configured
from peon.projects.models import (
    Finding,
    FindingSeverity,
    FindingStatus,
    Job,
    JobStatus,
    Project,
    ProjectStatus,
    RulesOfEngagement,
    StreamMessage,
    StreamMessageType,
)
from peon.projects.objectives import ObjectiveScheduler
from peon.projects.sandbox import ProjectSandbox
from peon.projects.services import PlanningService
from peon.projects.targets import (
    roe_add_path,
    roe_remove_path,
    add_candidates,
    format_targets,
    parse_target_lines,
    promote_candidates,
    provision_project_roe,
)
from peon.projects.worker import apply_directive
from peon.projects.workspaces import (
    delete_project_input,
    list_project_inputs,
    list_reports,
    resolve_report_path,
    save_project_input,
)
from peon.projects.project_status import ProjectOpsPayload
from peon.projects.catalog import SkillCards
from peon.projects.runtime_settings import PeonSettings

PROJECT_LIST_LIMIT = 50
PROJECT_JOBS_LIMIT = 40
STREAM_BOOTSTRAP_LIMIT = 120
STREAM_POLL_LIMIT = 200
HOME_PROJECT_LIMIT = 40
HOME_ALERT_LIMIT = 12


# --- Pages ---

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
            "sandbox_mode": "dedicated" if per_project else "shared",
        }
        for p in projects
    ]
    active_n = Project.objects.filter(status=ProjectStatus.ACTIVE).count()
    paused_n = Project.objects.filter(status=ProjectStatus.PAUSED).count()
    running_jobs = Job.objects.filter(status=JobStatus.RUNNING).count()
    failed_jobs = Job.objects.filter(status=JobStatus.FAILED).count()
    failed_recent = list(
        Job.objects.filter(status=JobStatus.FAILED)
        .exclude(error="")
        .select_related("project")[:HOME_ALERT_LIMIT]
    )
    stream_errors = list(
        StreamMessage.objects.filter(message_type=StreamMessageType.ERROR)
        .select_related("job", "job__project")
        .order_by("-created_at")[:HOME_ALERT_LIMIT]
    )
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
        _values, restart_changed = PeonSettings.update(updates)
        if restart_changed:
            restart_needed = True
            messages.warning(
                request,
                "Saved. Restart the worker service for Dramatiq thread changes to apply.",
            )
        else:
            messages.success(request, "Settings saved.")
        return redirect("settings_page")
    return render(
        request,
        "projects/settings.html",
        {
            "fields": PeonSettings.field_meta(),
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
            "available_skills": SkillCards.picker(),
            "selected_skills": SkillCards.required_names(),
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
    flashes: list[tuple[str, str]] = []

    # Uploads need a project id — create shell first without paths, then attach.
    project, scope, roe = PlanningService.create_project_shell(
        title=title,
        summary=summary,
        in_scope=in_scope,
        path_values=[],
        operator_supplied_scope=operator_scope,
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
        for s in request.POST.getlist("skill_names")
        if str(s).strip()
    ]
    required = SkillCards.required_names()
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
            result = PlanningService.run_llm_plan(
                description=brief,
                mode="project",
                title=project.title,
                summary=project.summary,
                in_scope=scope,
                exclusions=list(roe.exclusions) if roe else [],
                authorization=(roe.authorization_note if roe else ""),
                skills=picked or None,
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
            flashes.append(("error", msg))
            out["message"] = msg
            out["plan_ok"] = False
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
    return out


@require_GET
def project_detail(request: HttpRequest, pk) -> HttpResponse:
    project = get_object_or_404(Project.objects.select_related("roe"), pk=pk)
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
    attack_surface = engagement_graph(project, findings=graph_findings)
    return render(
        request,
        "projects/detail.html",
        {
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
            "nav": "projects",
            "stream_bootstrap": json.dumps(bootstrap),
        },
    )


@require_GET
def job_live(request: HttpRequest, pk, job_id) -> HttpResponse:
    project = get_object_or_404(Project, pk=pk)
    job = get_object_or_404(Job, pk=job_id, project=project)
    return render(
        request,
        "projects/job_live.html",
        {"project": project, "job": job, "nav": "projects"},
    )

# --- Control ---

@require_http_methods(["POST"])
def project_finding_triage(request: HttpRequest, pk, finding_id) -> HttpResponse:
    """Set finding status (closed set). JSON when requested — no full page reload."""
    project = get_object_or_404(Project, pk=pk)
    finding = get_object_or_404(Finding, pk=finding_id, project=project)
    status = (request.POST.get("status") or "").strip().lower()
    action = (request.POST.get("action") or "").strip().lower()
    updated = FindingStore().triage(finding, action, status=status)
    wants_json = request_wants_json(request)
    if updated is None:
        if wants_json:
            return JsonResponse(
                {"ok": False, "error": f"Invalid status: {status or action or '(empty)'}"},
                status=400,
            )
        messages.error(request, f"Unknown triage action: {action or status or '(empty)'}")
    else:
        if wants_json:
            counts = FindingStore.board_counts(project)
            return JsonResponse(
                {
                    "ok": True,
                    "id": str(updated.id),
                    "seq": updated.seq,
                    "status": updated.status,
                    "counts": counts,
                }
            )
        messages.success(request, f"FIND-{finding.seq} → {updated.status}")
    status_q = (request.POST.get("finding_status") or "open").strip()
    severity_q = (request.POST.get("finding_severity") or "all").strip()
    url = reverse("project_detail", kwargs={"pk": project.pk})
    return redirect(f"{url}?finding_status={status_q}&finding_severity={severity_q}#results")


@require_GET
def project_findings_json(request: HttpRequest, pk) -> JsonResponse:
    """Findings board rows for live filter / refresh without full page reload."""
    project = get_object_or_404(Project, pk=pk)
    board = FindingStore.board_query(project, request, default_status="open")
    return JsonResponse(
        {
            "findings": board["payload"],
            "counts": board["counts"],
            "statuses": list(FindingStatus.choices),
            "severities": list(FindingSeverity.choices),
        }
    )

@require_http_methods(["POST"])
def project_chat(request: HttpRequest, pk) -> HttpResponse:
    """HITL console: instruct running agents | replan | stop | reply to prompts."""
    from peon.projects.console_chat import http_handle_console_chat

    project = get_object_or_404(Project, pk=pk)
    return http_handle_console_chat(request, project)


@require_http_methods(["GET", "POST"])
def project_inputs(request: HttpRequest, pk) -> HttpResponse:
    """List / upload project-local input files (``workspace/inputs/`` only)."""
    project = get_object_or_404(Project, pk=pk)
    wants_json = request_wants_json(request)
    if request.method == "GET":
        return JsonResponse({"ok": True, "inputs": list_project_inputs(str(project.id))})

    if project.status == ProjectStatus.CANCELLED:
        if wants_json:
            return JsonResponse(
                {"ok": False, "error": "Project cancelled — uploads disabled."},
                status=409,
            )
        messages.error(request, "Project cancelled — uploads disabled.")
        return redirect(reverse("project_detail", kwargs={"pk": project.pk}) + "#reports")

    files = request.FILES.getlist("uploads") or request.FILES.getlist("file")
    if not files and request.FILES:
        files = list(request.FILES.values())
    if not files:
        if wants_json:
            return JsonResponse({"ok": False, "error": "No file uploaded."}, status=400)
        messages.error(request, "No file uploaded.")
        return redirect(reverse("project_detail", kwargs={"pk": project.pk}) + "#reports")

    saved_rows: list[dict] = []
    errors: list[str] = []
    for f in files:
        try:
            row = save_project_input(str(project.id), f)
            roe_add_path(project, row["sandbox_path"])
            saved_rows.append(row)
            # Surface in live stream on a recent/running job when possible.
            job = anchor_job(project)
            if job is not None:
                emit_job_stream(
                    job,
                    "log",
                    f"Uploaded {row['name']} → {row['sandbox_path']} (this project only)",
                    stream_meta(
                        project,
                        role="user",
                        event="project_upload",
                        path=row["sandbox_path"],
                    ),
                )
        except Exception as exc:
            errors.append(f"{getattr(f, 'name', 'file')}: {exc}")

    if wants_json:
        status = 200 if saved_rows else 400
        return JsonResponse(
            {
                "ok": bool(saved_rows),
                "inputs": list_project_inputs(str(project.id)),
                "saved": saved_rows,
                "errors": errors,
            },
            status=status,
        )
    for err in errors:
        messages.warning(request, err)
    if saved_rows:
        messages.success(request, f"Uploaded {len(saved_rows)} file(s) to this project.")
    return redirect(reverse("project_detail", kwargs={"pk": project.pk}) + "#reports")


@require_http_methods(["POST"])
def project_input_delete(request: HttpRequest, pk) -> HttpResponse:
    """Remove one file from this project's inputs/ tree."""
    project = get_object_or_404(Project, pk=pk)
    wants_json = request_wants_json(request)
    name = request_value(request, "name")

    sandbox_path = f"/workspace/inputs/{Path(name).name}" if name else ""
    ok = delete_project_input(str(project.id), name) if name else False
    if ok:
        roe_remove_path(project, sandbox_path)
    if wants_json:
        return JsonResponse(
            {
                "ok": ok,
                "inputs": list_project_inputs(str(project.id)),
                "error": None if ok else "File not found in this project.",
            },
            status=200 if ok else 404,
        )
    if ok:
        messages.success(request, f"Removed {name}")
    else:
        messages.error(request, "File not found in this project.")
    return redirect(reverse("project_detail", kwargs={"pk": project.pk}) + "#reports")


@require_http_methods(["POST"])
def project_roe(request: HttpRequest, pk) -> HttpResponseRedirect:
    project = get_object_or_404(Project, pk=pk)
    roe, _ = RulesOfEngagement.objects.get_or_create(project=project)
    action = (request.POST.get("roe_action") or "save").strip().lower()

    if action == "promote":
        raw = request.POST.getlist("promote_idx")
        try:
            indices = [int(x) for x in raw]
        except ValueError:
            indices = []
        n = promote_candidates(
            roe,
            indices=indices,
            all_candidates=(request.POST.get("promote_all") == "1"),
        )
        messages.success(request, f"Promoted {n} candidate(s) into in-scope")
        return redirect(reverse("project_detail", kwargs={"pk": project.pk}) + "#assets")

    if action == "add_candidates":
        n = add_candidates(roe, parse_target_lines(request.POST.get("candidates") or ""))
        if n:
            messages.success(request, f"Added {n} candidate(s)")
        else:
            messages.warning(request, "No candidates parsed")
        return redirect(reverse("project_detail", kwargs={"pk": project.pk}) + "#assets")

    in_scope = parse_target_lines(request.POST.get("in_scope") or "")
    exclusions = parse_target_lines(request.POST.get("exclusions") or "")
    seed = parse_target_lines(request.POST.get("seed") or "")
    authorization = (request.POST.get("authorization") or "").strip()
    if in_scope:
        roe.in_scope = in_scope
        roe.exclusions = exclusions
        roe.seed = seed
        roe.authorization_note = authorization
        roe.save()
        messages.success(request, "Rules of Engagement updated")
    else:
        roe.exclusions = exclusions
        roe.seed = seed
        if authorization:
            roe.authorization_note = authorization
        roe.in_scope = []
        roe.save()
        _, deduced = provision_project_roe(
            project,
            texts=[project.title, project.summary],
            exclusions=exclusions or None,
            authorization=authorization,
        )
        if deduced:
            messages.info(
                request,
                "Rules of Engagement deduced: "
                + ", ".join(format_targets(project.roe.in_scope)),
            )
        else:
            messages.warning(
                request,
                "Rules of Engagement saved with empty in-scope — active probe "
                "skills need at least one target value (type labels like "
                "ip:/person: are optional hints).",
            )
    return redirect(reverse("project_detail", kwargs={"pk": project.pk}) + "#assets")


@require_http_methods(["GET", "POST"])
def projects_bulk(request: HttpRequest) -> HttpResponseRedirect:
    """Bulk project actions. GET redirects home (e.g. after an interrupted POST)."""
    if request.method == "GET":
        return redirect("project_list")

    action = (request.POST.get("action") or "").strip().lower()
    ids = request.POST.getlist("project_ids")
    if action == "pause":
        n = ProjectLifecycle.bulk_pause_projects(ids)
        messages.success(request, f"Paused {n} project(s)")
    elif action == "resume":
        n = ProjectLifecycle.bulk_resume_projects(ids)
        messages.success(request, f"Resumed {n} project(s)")
    elif action in {"delete", "remove"}:
        n = ProjectLifecycle.bulk_delete_projects(ids)
        messages.success(
            request,
            f"Deleted {n} project(s) (sandbox cleanup queued on worker)",
        )
    else:
        messages.error(request, f"Unknown bulk action: {action or '(empty)'}")
    return redirect("project_list")


@require_http_methods(["POST"])
def jobs_bulk(request: HttpRequest, pk) -> HttpResponseRedirect:
    project = get_object_or_404(Project, pk=pk)
    action = (request.POST.get("action") or "").strip().lower()
    ids = request.POST.getlist("job_ids")
    if action in {"pause", "resume", "kill", "queue"}:
        if action in {"resume", "queue"} and not ProjectLifecycle.project_accepts_work(project):
            messages.error(
                request, f"Project is {project.status} — resume the project first"
            )
            return redirect("project_detail", pk=project.pk)
        n = ProjectLifecycle.bulk_steer_jobs(project, ids, action)
        label = {"queue": "Queued"}.get(action, f"{action.title()}d")
        messages.success(request, f"{label} {n} job(s)")
    elif action in {"delete", "remove"}:
        n = ProjectLifecycle.bulk_delete_jobs(project, ids)
        messages.success(request, f"Removed {n} job(s)")
    else:
        messages.error(request, f"Unknown bulk action: {action or '(empty)'}")
    return redirect("project_detail", pk=project.pk)


@require_http_methods(["POST"])
def project_control(request: HttpRequest, pk) -> HttpResponseRedirect:
    project = get_object_or_404(Project, pk=pk)
    action = (request.POST.get("action") or "").strip().lower()
    if action == "pause":
        ProjectLifecycle.pause_project(project)
        messages.success(request, f"Paused project {project.title}")
        return redirect("project_detail", pk=project.pk)
    if action == "resume":
        ProjectLifecycle.resume_project(project)
        messages.success(request, f"Resumed project {project.title}")
        return redirect("project_detail", pk=project.pk)
    if action == "run_next":
        if project.status in {ProjectStatus.PAUSED, ProjectStatus.CANCELLED}:
            messages.error(request, f"Project is {project.status}")
            return redirect("project_detail", pk=project.pk)
        job = ObjectiveScheduler().enqueue_next(project)
        if job is None:
            messages.info(request, "No ready objective to run")
        else:
            messages.success(request, f"Queued {job.title}")
        return redirect("project_detail", pk=project.pk)
    if action == "replan":
        if not llm_configured():
            messages.error(request, "Set OPENROUTER_API_KEY to replan")
            return redirect("project_detail", pk=project.pk)
        brief = (request.POST.get("description") or "").strip()
        try:
            result = ProjectLifecycle.replan_from_prompt(project, brief)
            messages.success(
                request,
                f"Replanned — {result.get('objectives', 0)} objectives; "
                f"next job {result.get('primary_job_id') or '(none)'}",
            )
        except Exception as exc:
            messages.error(request, f"Replan failed: {exc}")
        return redirect("project_detail", pk=project.pk)
    if action in {"delete", "remove"}:
        title = project.title
        info = ProjectLifecycle.delete_project(project)
        sandbox = info.get("sandbox") or {}
        extra = ""
        if sandbox.get("action") == "removed":
            extra = f" (sandbox removed: {sandbox.get('name')})"
        elif sandbox.get("action") == "queued":
            extra = f" (sandbox cleanup queued: {sandbox.get('name')})"
        elif sandbox.get("action") == "missing":
            extra = " (no sandbox container)"
        elif sandbox.get("action") == "skipped":
            extra = f" (sandbox skip: {sandbox.get('reason')})"
        elif sandbox.get("action") == "error":
            errs = "; ".join(sandbox.get("errors") or []) or "unknown"
            messages.warning(
                request,
                f"Deleted project {title}, but sandbox cleanup failed: {errs}",
            )
            return redirect("project_list")
        messages.success(request, f"Deleted project {title}{extra}")
        return redirect("project_list")
    messages.error(request, f"Unknown project action: {action or '(empty)'}")
    return redirect("project_detail", pk=project.pk)

# --- Job actions ---

@require_http_methods(["POST"])
def job_start(request: HttpRequest, pk, job_id) -> HttpResponse:
    """Re-run a job (optional operator command). Allowed on finished projects."""
    project = get_object_or_404(Project, pk=pk)
    job = get_object_or_404(Job, pk=job_id, project=project)
    wants_json = request_wants_json(request)
    command = request_value(request, "command") or request_value(request, "description")

    try:
        ProjectLifecycle.rerun_job(job, command=command)
    except RuntimeError as exc:
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=409)
        messages.error(request, str(exc))
        return redirect("project_detail", pk=project.pk)

    if wants_json:
        return JsonResponse(
            {
                "ok": True,
                "job_id": str(job.id),
                "status": JobStatus.PENDING,
                "command": command,
            }
        )
    messages.success(request, f"Re-run queued: {job.title}")
    return redirect("project_detail", pk=project.pk)


@require_http_methods(["POST"])
def job_remove(request: HttpRequest, pk, job_id) -> HttpResponseRedirect:
    project = get_object_or_404(Project, pk=pk)
    job = get_object_or_404(Job, pk=job_id, project=project)
    title = job.title
    ProjectLifecycle.delete_job(job)
    messages.success(request, f"Removed job {title}")
    return redirect("project_detail", pk=project.pk)


@require_http_methods(["POST"])
def job_steer(request: HttpRequest, pk, job_id) -> HttpResponseRedirect:
    project = get_object_or_404(Project, pk=pk)
    job = get_object_or_404(Job, pk=job_id, project=project)
    action = (request.POST.get("action") or "").strip().lower()
    if action == "resume" and not ProjectLifecycle.project_accepts_work(project):
        messages.error(
            request, f"Project is {project.status} — resume the project first"
        )
        return redirect("project_detail", pk=project.pk)
    apply_directive(job, action)
    job.refresh_from_db()
    messages.success(request, f"Job {action or 'updated'} → {job.status}")
    next_url = (request.POST.get("next") or "").strip()
    if next_url == "project":
        return redirect("project_detail", pk=project.pk)
    return redirect("job_live", pk=project.pk, job_id=job.pk)

# --- JSON polls ---

@require_GET
def project_jobs_json(request: HttpRequest, pk) -> JsonResponse:
    project = get_object_or_404(Project, pk=pk)
    return JsonResponse(ProjectOpsPayload.for_project(project, jobs_limit=PROJECT_JOBS_LIMIT))


@require_GET
def project_messages_json(request: HttpRequest, pk) -> JsonResponse:
    project = get_object_or_404(Project, pk=pk)
    after = request.GET.get("after") or "0"
    try:
        after_id = int(after)
    except (TypeError, ValueError):
        after_id = 0
    return JsonResponse(
        {
            "messages": project_message_dicts(
                project, after_id=after_id, limit=STREAM_POLL_LIMIT
            )
        }
    )


@require_GET
def job_live_json(request: HttpRequest, pk, job_id) -> JsonResponse:
    project = get_object_or_404(Project, pk=pk)
    job = get_object_or_404(Job, pk=job_id, project=project)
    return JsonResponse({**ProjectOpsPayload.job_payload(job), "result": job.result})




# --- Reports ---

@require_GET
def project_report_view(request: HttpRequest, pk, file_path: str) -> HttpResponse:
    """In-browser report viewer (Markdown rendered client-side; other text as pre)."""
    project = get_object_or_404(Project, pk=pk)
    path = resolve_report_path(str(project.id), file_path)
    if path is None:
        raise Http404("Report not found")
    text = path.read_text(encoding="utf-8", errors="replace")
    is_md = path.suffix.lower() in {".md", ".markdown"}
    return render(
        request,
        "projects/report_view.html",
        {
            "project": project,
            "report_name": path.name,
            "report_path": file_path,
            "report_text": text,
            "is_markdown": is_md,
            "download_url": reverse(
                "project_report_file", kwargs={"pk": project.id, "file_path": file_path}
            ),
            "nav": "projects",
        },
    )


@require_GET
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

