"""Operator UI control POSTs + job actions / JSON polls."""

from __future__ import annotations

from pathlib import Path

from django.contrib import messages
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from orchestrator.utils.llm import llm_configured
from peon.projects.console import http_handle_console_chat
from peon.projects.crew_control import (
    pause_project,
    replan_project,
    reprompt_manager,
    restart_project,
    resume_project,
    stop_project,
)
from peon.projects.findings import FindingStore
from peon.projects.http_helpers import (
    flash_json,
    parse_json_body,
    request_value,
    scoped_get_or_404,
    with_project_context,
)
from peon.projects.http_helpers import (
    wants_json as request_wants_json,
)
from peon.projects.lifecycle import (
    apply_directive,
    bulk_delete_jobs,
    bulk_delete_projects,
    bulk_pause_projects,
    bulk_restart_projects,
    bulk_resume_projects,
    bulk_steer_jobs,
    bulk_stop_projects,
    delete_job,
    delete_project,
    project_accepts_work,
    rerun_job,
)
from peon.projects.mcp_ops import save_project_mcp_from_post
from peon.projects.models import (
    INACTIVE_PROJECT_STATUSES,
    Finding,
    FindingSeverity,
    FindingStatus,
    Job,
    JobStatus,
    Project,
    ProjectStatus,
)
from peon.projects.objectives import ObjectiveScheduler
from peon.projects.project_pages import PROJECT_JOBS_LIMIT, STREAM_POLL_LIMIT
from peon.projects.project_status import ProjectOpsPayload
from peon.projects.roe_ops import (
    add_candidates,
    get_or_create_roe,
    promote_candidates,
    provision_project_roe,
    roe_add_path,
    roe_remove_path,
)
from peon.projects.streaming import (
    anchor_job,
    emit_job_stream,
    project_message_dicts,
    stream_meta,
)
from peon.projects.target_shapes import (
    format_targets,
    parse_target_lines,
)
from peon.projects.workspaces import delete_project_input, list_project_inputs, save_project_input


@require_http_methods(["POST"])
@with_project_context
def project_finding_triage(request: HttpRequest, pk, finding_id) -> HttpResponse:
    """Set finding status (closed set). JSON when requested — no full page reload."""
    project = get_object_or_404(Project, pk=pk)
    finding = scoped_get_or_404(Finding, project, pk=finding_id)
    status = (request.POST.get("status") or "").strip().lower()
    action = (request.POST.get("action") or "").strip().lower()
    severity = (request.POST.get("severity") or "").strip().lower()
    wants_json = request_wants_json(request)
    store = FindingStore()
    if severity and not (status or action):
        # Operator re-rates the finding (status unchanged).
        updated = store.set_severity(finding, severity)
        what = f"severity {severity}"
    else:
        updated = store.triage(finding, action, status=status)
        if updated is not None and severity:
            updated = store.set_severity(updated, severity) or updated
        what = f"status {status or action or '(empty)'}"
    if updated is None:
        if wants_json:
            return JsonResponse(
                {"ok": False, "error": f"Invalid {what}"},
                status=400,
            )
        messages.error(request, f"Unknown triage action: {what}")
    else:
        if wants_json:
            counts = FindingStore.board_counts(project)
            return JsonResponse(
                {
                    "ok": True,
                    "id": str(updated.id),
                    "seq": updated.seq,
                    "status": updated.status,
                    "severity": updated.severity,
                    "counts": counts,
                }
            )
        messages.success(request, f"FIND-{finding.seq} → {updated.status} / {updated.severity}")
    status_q = (request.POST.get("finding_status") or "open").strip()
    severity_q = (request.POST.get("finding_severity") or "all").strip()
    url = reverse("project_detail", kwargs={"pk": project.pk})
    return redirect(f"{url}?finding_status={status_q}&finding_severity={severity_q}#results")


@require_GET
@with_project_context
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
@with_project_context
def project_chat(request: HttpRequest, pk) -> HttpResponse:
    """HITL console: instruct running agents | replan | stop | reply to prompts."""

    project = get_object_or_404(Project, pk=pk)
    return http_handle_console_chat(request, project)


@require_http_methods(["GET", "POST"])
@with_project_context
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
@with_project_context
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
@with_project_context
def project_mcp(request: HttpRequest, pk) -> HttpResponse:
    """Save per-project MCP env values (catalog holds names only)."""
    project = get_object_or_404(Project, pk=pk)
    wants_json = request_wants_json(request)
    try:
        if wants_json and request.content_type and "application/json" in request.content_type:
            body = parse_json_body(request, strict=True)
            mcp_id = str(body.get("mcp_id") or "").strip()
            env = body.get("env") if isinstance(body.get("env"), dict) else {}
            from peon.projects.mcp_ops import save_project_mcp_env

            saved = save_project_mcp_env(project.pk, mcp_id, env)
            return JsonResponse({"ok": True, "mcp_id": mcp_id, "env_keys": sorted(saved)})
        n = save_project_mcp_from_post(project.pk, request.POST)
    except ValueError as exc:
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, str(exc))
        return redirect(reverse("project_detail", kwargs={"pk": project.pk}) + "#settings")
    if wants_json:
        return JsonResponse({"ok": True, "updated": n})
    if n:
        messages.success(request, f"Saved MCP configuration for {n} server(s)")
    else:
        messages.info(request, "No MCP env fields submitted")
    return redirect(reverse("project_detail", kwargs={"pk": project.pk}) + "#settings")


@require_http_methods(["POST"])
@with_project_context
@flash_json
def project_roe(request: HttpRequest, pk) -> HttpResponseRedirect:
    project = get_object_or_404(Project, pk=pk)
    # RoE lives on project SQLite — never get_or_create on the platform default DB.
    roe = get_or_create_roe(project)
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
                "Rules of Engagement deduced: " + ", ".join(format_targets(project.roe.in_scope)),
            )
        else:
            messages.warning(
                request,
                "Rules of Engagement saved with empty in-scope — active probe "
                "roles need at least one target value (type labels like "
                "ip:/person: are optional hints).",
            )
    return redirect(reverse("project_detail", kwargs={"pk": project.pk}) + "#assets")


def _safe_next(request: HttpRequest) -> str:
    from django.utils.http import url_has_allowed_host_and_scheme

    target = (request.POST.get("next") or "").strip()
    ok = url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    return target if target and ok else ""


def _after_control(
    request: HttpRequest, project: Project, *, deleted: bool = False
) -> HttpResponseRedirect:
    nxt = _safe_next(request)
    if nxt:
        return redirect(nxt)
    if deleted:
        return redirect("project_list")
    return redirect("project_detail", pk=project.pk)


@require_http_methods(["GET", "POST"])
def projects_bulk(request: HttpRequest) -> HttpResponseRedirect:
    """Bulk project actions. GET redirects home (e.g. after an interrupted POST)."""
    if request.method == "GET":
        return redirect("project_list")

    action = (request.POST.get("action") or "").strip().lower()
    ids = request.POST.getlist("project_ids")
    if action == "pause":
        n = bulk_pause_projects(ids)
        messages.success(request, f"Paused {n} project(s)")
    elif action == "resume":
        n = bulk_resume_projects(ids)
        messages.success(request, f"Resumed {n} project(s)")
    elif action == "stop":
        n = bulk_stop_projects(ids)
        messages.success(request, f"Stopped {n} project(s)")
    elif action == "restart":
        n = bulk_restart_projects(ids)
        messages.success(request, f"Restarted {n} project(s)")
    elif action in {"delete", "remove"}:
        n = bulk_delete_projects(ids)
        messages.success(
            request,
            f"Deleted {n} project(s) (sandbox cleanup queued on worker)",
        )
    else:
        messages.error(request, f"Unknown bulk action: {action or '(empty)'}")
    return redirect(_safe_next(request) or "project_list")


@require_http_methods(["POST"])
@with_project_context
def jobs_bulk(request: HttpRequest, pk) -> HttpResponseRedirect:
    project = get_object_or_404(Project, pk=pk)
    action = (request.POST.get("action") or "").strip().lower()
    ids = request.POST.getlist("job_ids")
    if action in {"pause", "resume", "kill", "queue"}:
        if action in {"resume", "queue"} and not project_accepts_work(project):
            messages.error(request, f"Project is {project.status} — resume the project first")
            return redirect("project_detail", pk=project.pk)
        n = bulk_steer_jobs(project, ids, action)
        label = {"queue": "Queued"}.get(action, f"{action.title()}d")
        messages.success(request, f"{label} {n} job(s)")
    elif action in {"delete", "remove"}:
        n = bulk_delete_jobs(project, ids)
        messages.success(request, f"Removed {n} job(s)")
    else:
        messages.error(request, f"Unknown bulk action: {action or '(empty)'}")
    return redirect("project_detail", pk=project.pk)


@require_http_methods(["POST"])
@with_project_context
@flash_json
def project_control(request: HttpRequest, pk) -> HttpResponseRedirect:
    project = get_object_or_404(Project, pk=pk)
    action = (request.POST.get("action") or "").strip().lower()
    if action == "pause":
        if project.status != ProjectStatus.ACTIVE:
            messages.error(request, "Pause applies to a running project")
            return _after_control(request, project)
        pause_project(project)
        messages.success(request, f"Paused project {project.title}")
        return _after_control(request, project)
    if action == "resume":
        if project.status != ProjectStatus.PAUSED:
            messages.error(request, "Resume applies to a paused project")
            return _after_control(request, project)
        steer = (request.POST.get("description") or request.POST.get("steer") or "").strip()
        resume_project(project, steer=steer)
        messages.success(request, f"Resumed project {project.title}")
        return _after_control(request, project)
    if action == "stop":
        if project.status not in {ProjectStatus.ACTIVE, ProjectStatus.PAUSED}:
            messages.error(request, "Stop applies to a running or paused project")
            return _after_control(request, project)
        stop_project(project)
        messages.success(request, f"Stopped project {project.title}")
        return _after_control(request, project)
    if action == "restart":
        try:
            restart_project(project)
        except (RuntimeError, ValueError) as exc:
            messages.error(request, f"Restart failed: {exc}")
            return _after_control(request, project)
        messages.success(request, f"Restarted project {project.title}")
        return _after_control(request, project)
    if action == "run_next":
        if project.status in INACTIVE_PROJECT_STATUSES:
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
            result = replan_project(project, brief)
            messages.success(
                request,
                f"Replanned ({result.get('mode')}) — "
                f"next job {result.get('primary_job_id') or '(none)'}",
            )
        except Exception as exc:
            messages.error(request, f"Replan failed: {exc}")
        return redirect("project_detail", pk=project.pk)
    if action == "reprompt":
        note = (request.POST.get("description") or request.POST.get("steer") or "").strip()
        try:
            result = reprompt_manager(project, note)
            messages.success(
                request,
                f"Reprompted manager job {result.get('primary_job_id') or '(none)'}",
            )
        except Exception as exc:
            messages.error(request, f"Reprompt failed: {exc}")
        return redirect("project_detail", pk=project.pk)
    if action in {"delete", "remove"}:
        title = project.title
        info = delete_project(project)
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
        return _after_control(request, project, deleted=True)
    messages.error(request, f"Unknown project action: {action or '(empty)'}")
    return redirect("project_detail", pk=project.pk)


@require_http_methods(["POST"])
@with_project_context
def job_start(request: HttpRequest, pk, job_id) -> HttpResponse:
    """Re-run a job (optional operator command). Allowed on finished projects."""
    project = get_object_or_404(Project, pk=pk)
    job = scoped_get_or_404(Job, project, pk=job_id)
    wants_json = request_wants_json(request)
    command = request_value(request, "command") or request_value(request, "description")

    try:
        rerun_job(job, command=command)
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
@with_project_context
def job_remove(request: HttpRequest, pk, job_id) -> HttpResponseRedirect:
    project = get_object_or_404(Project, pk=pk)
    job = scoped_get_or_404(Job, project, pk=job_id)
    title = job.title
    delete_job(job)
    messages.success(request, f"Removed job {title}")
    return redirect("project_detail", pk=project.pk)


@require_http_methods(["POST"])
@with_project_context
@flash_json
def job_steer(request: HttpRequest, pk, job_id) -> HttpResponseRedirect:
    project = get_object_or_404(Project, pk=pk)
    job = scoped_get_or_404(Job, project, pk=job_id)
    action = (request.POST.get("action") or "").strip().lower()
    if action == "resume" and not project_accepts_work(project):
        messages.error(request, f"Project is {project.status} — resume the project first")
        return redirect("project_detail", pk=project.pk)
    apply_directive(job, action)
    job.refresh_from_db()
    messages.success(request, f"Job {action or 'updated'} → {job.status}")
    next_url = (request.POST.get("next") or "").strip()
    if next_url == "project":
        return redirect("project_detail", pk=project.pk)
    return redirect("job_live", pk=project.pk, job_id=job.pk)


@require_GET
@with_project_context
def project_jobs_json(request: HttpRequest, pk) -> JsonResponse:
    project = get_object_or_404(Project, pk=pk)
    return JsonResponse(ProjectOpsPayload.for_project(project, jobs_limit=PROJECT_JOBS_LIMIT))


@require_GET
@with_project_context
def project_messages_json(request: HttpRequest, pk) -> JsonResponse:
    project = get_object_or_404(Project, pk=pk)
    after = request.GET.get("after") or "0"
    try:
        after_id = int(after)
    except (TypeError, ValueError):
        after_id = 0
    return JsonResponse(
        {"messages": project_message_dicts(project, after_id=after_id, limit=STREAM_POLL_LIMIT)}
    )


@require_GET
@with_project_context
def job_messages_json(request: HttpRequest, pk, job_id) -> JsonResponse:
    """One agent's own stream history (the project feed only bootstraps recent lines)."""
    from peon.projects.streaming import job_message_dicts

    project = get_object_or_404(Project, pk=pk)
    job = scoped_get_or_404(Job, project, pk=job_id)
    try:
        before = int(request.GET.get("before") or 0)
        limit = int(request.GET.get("limit") or 200)
    except (TypeError, ValueError):
        before, limit = 0, 200
    return JsonResponse(
        {
            "job_id": str(job.pk),
            "messages": job_message_dicts(project, str(job.pk), before_id=before, limit=limit),
        }
    )


@require_GET
@with_project_context
def job_live_json(request: HttpRequest, pk, job_id) -> JsonResponse:
    project = get_object_or_404(Project, pk=pk)
    job = scoped_get_or_404(Job, project, pk=job_id)
    return JsonResponse({**ProjectOpsPayload.job_payload(job), "result": job.result})


@require_http_methods(["GET", "POST"])
@with_project_context
def project_plan_api(request: HttpRequest, pk) -> JsonResponse:
    """Load / save / accept durable plan agent graph."""
    from peon.projects.plan_graph import (
        accept_plan,
        plan_graph_payload,
        save_plan_agents,
    )

    project = get_object_or_404(Project, pk=pk)
    if request.method == "GET":
        return JsonResponse(plan_graph_payload(project))

    try:
        body = parse_json_body(request, strict=True)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    action = str(body.get("action") or "save").strip().lower()
    try:
        if action == "save":
            agents = body.get("agents") or []
            if not isinstance(agents, list):
                return JsonResponse({"ok": False, "error": "agents must be a list"}, status=400)
            save_plan_agents(project, agents)
            return JsonResponse({"ok": True, **plan_graph_payload(project)})
        if action == "accept":
            agents = body.get("agents")
            if isinstance(agents, list) and agents:
                save_plan_agents(project, agents)
            result = accept_plan(project)
            result["redirect"] = reverse("project_detail", kwargs={"pk": project.pk})
            return JsonResponse(result)
        if action == "schedule_upsert":
            from peon.projects.models import Objective, ProjectSchedule
            from peon.projects.schedules import arm_schedule, create_schedule

            sid = str(body.get("id") or "").strip()
            obj_id = str(body.get("objective_id") or "").strip()
            objective = None
            if obj_id:
                from peon.projects.db import qs_using

                objective = (
                    qs_using(Objective, project.id).filter(project_id=project.id, pk=obj_id).first()
                )
            if sid:
                row = scoped_get_or_404(ProjectSchedule, project, pk=sid)
                row.title = str(body.get("title") or row.title)[:255]
                row.role_id = str(body.get("role_id") or row.role_id)[:128]
                row.brief = str(body.get("brief") or row.brief)
                row.kind = str(body.get("kind") or row.kind)
                row.cron = str(body.get("cron") or row.cron)[:64]
                row.interval_seconds = int(
                    body.get("interval_seconds") or row.interval_seconds or 0
                )
                row.enabled = bool(body.get("enabled", row.enabled))
                row.skip_if_running = bool(body.get("skip_if_running", row.skip_if_running))
                if objective is not None:
                    row.objective = objective
                row.save()
                arm_schedule(row)
            else:
                create_schedule(
                    project,
                    title=str(body.get("title") or ""),
                    role_id=str(body.get("role_id") or ""),
                    brief=str(body.get("brief") or ""),
                    kind=str(body.get("kind") or "interval"),
                    cron=str(body.get("cron") or ""),
                    interval_seconds=int(body.get("interval_seconds") or 0),
                    objective=objective,
                    enabled=bool(body.get("enabled", True)),
                    skip_if_running=bool(body.get("skip_if_running", True)),
                )
            return JsonResponse({"ok": True, **plan_graph_payload(project)})
        if action == "schedule_delete":
            from peon.projects.models import ProjectSchedule

            sid = str(body.get("id") or "").strip()
            if not sid:
                return JsonResponse({"ok": False, "error": "id required"}, status=400)
            from peon.projects.db import qs_using

            qs_using(ProjectSchedule, project.id).filter(pk=sid, project_id=project.id).delete()
            return JsonResponse({"ok": True, **plan_graph_payload(project)})
        return JsonResponse({"ok": False, "error": f"unknown action {action}"}, status=400)
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)


@require_GET
@with_project_context
def job_role_suggestions(request: HttpRequest, pk, job_id) -> JsonResponse:
    """Suggest specialist roles for an operator 'add child' spawn under this job."""
    from peon.projects.operator_spawn import suggest_roles_for_parent

    project = get_object_or_404(Project, pk=pk)
    parent = scoped_get_or_404(Job, project, pk=job_id)
    roles = suggest_roles_for_parent(parent)
    return JsonResponse(
        {
            "ok": True,
            "parent_job_id": str(parent.id),
            "parent_roles": list(parent.role_ids or []),
            "roles": roles,
        }
    )


@require_http_methods(["POST"])
@with_project_context
def job_add_child(request: HttpRequest, pk, job_id) -> JsonResponse:
    """Operator add-child: new objective + child Job under parent, analyzer stays last."""
    from peon.projects.operator_spawn import add_child_agent

    project = get_object_or_404(Project, pk=pk)
    parent = scoped_get_or_404(Job, project, pk=job_id)
    try:
        body = parse_json_body(request, strict=True)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    try:
        skills_raw = body.get("skill_ids") or body.get("skills") or []
        if not isinstance(skills_raw, list):
            skills_raw = []
        result = add_child_agent(
            project,
            parent,
            role_id=str(body.get("role_id") or "").strip(),
            title=str(body.get("title") or "").strip(),
            prompt=str(body.get("prompt") or body.get("description") or "").strip(),
            skill_ids=[str(s).strip() for s in skills_raw if str(s).strip()],
        )
    except (ValueError, RuntimeError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse(result)


@require_GET
def pending_inputs_global(request: HttpRequest) -> JsonResponse:
    """Cross-project open HITL prompts for the global popup."""
    from peon.projects.hitl import pending_inputs_global as list_pending

    items = list_pending()
    return JsonResponse({"ok": True, "pending": items, "count": len(items)})


@require_http_methods(["POST"])
def pending_input_decide(request: HttpRequest) -> JsonResponse:
    """Approve / deny / edit a pending OperatorPrompt from the global popup."""
    from peon.projects.hitl import resolve_operator_prompt
    from peon.projects.models import OperatorPrompt

    try:
        body = parse_json_body(request, strict=True)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    prompt_id = str(body.get("id") or "").strip()
    decision = str(body.get("decision") or "approve").strip().lower()
    reply = str(body.get("reply") or "").strip()
    if not prompt_id or prompt_id.startswith("obj-"):
        return JsonResponse(
            {
                "ok": False,
                "error": "Blocked objectives need project chat / replan; open the project.",
            },
            status=400,
        )
    from peon.projects.db import get_operator_prompt

    try:
        prompt = get_operator_prompt(prompt_id, unresolved_only=True)
    except OperatorPrompt.DoesNotExist:
        return JsonResponse({"ok": False, "error": "prompt not found"}, status=404)
    if decision == "deny":
        text = reply or "DENIED by operator"
    elif decision == "edit":
        if not reply:
            return JsonResponse({"ok": False, "error": "edit requires reply text"}, status=400)
        text = reply
    else:
        if not reply and list(prompt.options or []):
            # A bare "approve" cannot answer a multiple-choice question.
            return JsonResponse(
                {"ok": False, "error": "choose one of the options or type an answer"},
                status=400,
            )
        text = reply or "APPROVED by operator"
    try:
        resolve_operator_prompt(prompt, text)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse(
        {
            "ok": True,
            "id": str(prompt.id),
            "decision": decision,
            "href": reverse("project_detail", kwargs={"pk": prompt.project_id}) + "#war-chat",
        }
    )
