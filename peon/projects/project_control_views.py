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

from peon.projects.findings import FindingStore
from peon.projects.http_helpers import (
    request_value,
    wants_json as request_wants_json,
)
from peon.projects.project_status import ProjectOpsPayload
from peon.projects.streaming import (
    anchor_job,
    emit_job_stream,
    project_message_dicts,
    stream_meta,
)
from peon.projects.lifecycle import ProjectLifecycle, apply_directive
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
)
from peon.projects.objectives import ObjectiveScheduler
from peon.projects.roe_ops import (
    roe_add_path,
    roe_remove_path,
    add_candidates,
    promote_candidates,
    provision_project_roe,
)
from peon.projects.target_shapes import (
    format_targets,
    parse_target_lines,
)
from peon.projects.workspaces import (
    delete_project_input,
    list_project_inputs,
    save_project_input,
)
from peon.projects.console import http_handle_console_chat
from peon.projects.project_pages import PROJECT_JOBS_LIMIT, STREAM_POLL_LIMIT
from peon.projects.crew_control import (
    pause_project as crew_pause,
    replan_project as crew_replan,
    reprompt_manager,
    resume_project as crew_resume,
)


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
                "active probe roles need at least one target value (type labels like "
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

        crew_pause(project)
        messages.success(request, f"Paused project {project.title}")
        return redirect("project_detail", pk=project.pk)
    if action == "resume":

        steer = (request.POST.get("description") or request.POST.get("steer") or "").strip()
        crew_resume(project, steer=steer)
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
            result = crew_replan(project, brief)
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




