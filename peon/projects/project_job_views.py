"""Operator UI job actions and JSON poll endpoints."""

from __future__ import annotations

from django.contrib import messages
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_GET, require_http_methods

from peon.projects.http_helpers import (
    request_value,
    wants_json as request_wants_json,
)
from peon.projects.lifecycle import ProjectLifecycle
from peon.projects.models import Job, JobStatus, Project
from peon.projects.project_status import ProjectOpsPayload
from peon.projects.streaming import project_message_dicts
from peon.projects.lifecycle import apply_directive

from peon.projects.project_pages import PROJECT_JOBS_LIMIT, STREAM_POLL_LIMIT



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




