"""Operator UI: pages, control POSTs, JSON polls, report download."""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    JsonResponse,
)
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.views.decorators.http import require_GET, require_http_methods

from peon.projects.findings import FindingStore
from peon.projects.lifecycle import ProjectLifecycle
from peon.projects.llm_gate import llm_configured
from peon.projects.models import (
    TERMINAL_JOB_STATUSES,
    Finding,
    FindingSeverity,
    FindingStatus,
    Job,
    JobStatus,
    Objective,
    ObjectiveStatus,
    Project,
    ProjectStatus,
    RulesOfEngagement,
    StreamMessage,
)
from peon.projects.objectives import ObjectiveScheduler
from peon.projects.services import PlanningService
from peon.projects.streaming import message_to_dict
from peon.projects.targets import (
    add_candidates,
    coerce_targets,
    extract_scope_assets,
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
from peon.projects.project_status import ProjectStatus as ProjectStatusPayload
from peon.projects.catalog import SkillCards, ToolCards
from peon.projects.runtime_settings import PeonSettings

PROJECT_LIST_LIMIT = 50
PROJECT_JOBS_LIMIT = 40
STREAM_BOOTSTRAP_LIMIT = 120
STREAM_POLL_LIMIT = 200
PALETTE_PROJECT_LIMIT = 40
PALETTE_FINDING_LIMIT = 40
PALETTE_SKILL_LIMIT = 40
PALETTE_TOOL_LIMIT = 40


# --- Pages ---

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
        title = (request.POST.get("title") or "").strip() or "untitled"
        summary = (request.POST.get("summary") or "").strip()
        in_scope = parse_target_lines(request.POST.get("in_scope") or "")
        project = Project.objects.create(
            title=title,
            summary=summary,
            status=ProjectStatus.ACTIVE,
        )
        if not in_scope:
            in_scope = extract_scope_assets(title, summary)

        uploaded_paths: list[str] = []
        for f in request.FILES.getlist("uploads"):
            try:
                saved = save_project_input(str(project.id), f)
                uploaded_paths.append(saved["sandbox_path"])
            except Exception as exc:
                messages.warning(request, f"Upload skipped ({getattr(f, 'name', '?')}): {exc}")

        seed: list = []
        if summary and not in_scope:
            seed.append({"type": "other", "value": summary[:500]})
        for sp in uploaded_paths:
            seed.append({"type": "path", "value": sp})
            in_scope = list(in_scope) + [{"type": "path", "value": sp}]

        RulesOfEngagement.objects.create(
            project=project,
            in_scope=in_scope,
            seed=seed,
            authorization_note=(
                "Auto-deduced from summary (operator did not supply RoE)."
                if in_scope and not (request.POST.get("in_scope") or "").strip()
                else ""
            ),
        )
        provision_project_roe(project, texts=[title, summary])
        project.refresh_from_db()
        roe = getattr(project, "roe", None)
        scope = list(roe.in_scope) if roe else in_scope

        if in_scope and not (request.POST.get("in_scope") or "").strip() and not uploaded_paths:
            messages.info(request, "RoE deduced: " + ", ".join(format_targets(in_scope)))
        if uploaded_paths:
            messages.info(
                request,
                "Stored "
                + str(len(uploaded_paths))
                + " file(s) under project inputs (sandbox paths in RoE).",
            )

        picked = [
            str(s).strip()
            for s in request.POST.getlist("skill_names")
            if str(s).strip()
        ]
        # Core skills are always on; UI locks them but POST can omit disabled fields.
        required = SkillCards.required_names()
        picked = list(dict.fromkeys([*required, *picked]))
        brief = (summary or title).strip()
        if uploaded_paths:
            brief = (
                brief
                + "\n\nOperator uploaded sample(s) (project-local only):\n"
                + "\n".join(f"- {p}" for p in uploaded_paths)
            ).strip()
        if llm_configured() and brief:
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
                messages.success(
                    request,
                    f"Created {project.title} — queued "
                    f"{result.job.title if result.job else 'no ready objective'} "
                    f"({len(result.objectives or [])} objectives)",
                )
            except Exception as exc:
                messages.error(request, f"Created {project.title} but plan failed: {exc}")
        else:
            if not llm_configured():
                messages.warning(
                    request,
                    f"Created {project.title} — set OPENROUTER_API_KEY to auto-plan",
                )
            else:
                messages.success(request, f"Created project {project.title}")
        return redirect("project_detail", pk=project.pk)

    return render(
        request,
        "projects/create.html",
        {
            "nav": "projects",
            "available_skills": SkillCards.picker(),
            "selected_skills": SkillCards.required_names(),
        },
    )

@require_GET
def project_detail(request: HttpRequest, pk) -> HttpResponse:
    project = get_object_or_404(Project.objects.select_related("roe"), pk=pk)
    ops = ProjectStatusPayload.for_project(project, jobs_limit=PROJECT_JOBS_LIMIT)
    objectives = list(project.objectives.all())
    roe = getattr(project, "roe", None)
    recent = (
        StreamMessage.objects.filter(job__project=project)
        .select_related("job")
        .order_by("-id")[:STREAM_BOOTSTRAP_LIMIT]
    )
    bootstrap = []
    for m in reversed(list(recent)):
        job = getattr(m, "job", None)
        if job is not None:
            m._payload_title = job.title  # type: ignore[attr-defined]
            m._payload_status = job.status  # type: ignore[attr-defined]
        m._payload_project = str(project.id)  # type: ignore[attr-defined]
        bootstrap.append(message_to_dict(m))
    reports = list_reports(str(project.id))
    status_f = (request.GET.get("finding_status") or "open").strip().lower()
    severity_f = (request.GET.get("finding_severity") or "all").strip().lower()
    findings = FindingStore().board(
        project, status=status_f, severity=severity_f
    )
    finding_counts = project.findings.aggregate(
        all=Count("id"),
        open=Count("id", filter=Q(status=FindingStatus.OPEN)),
        confirmed=Count("id", filter=Q(status=FindingStatus.CONFIRMED)),
    )
    has_next_objective = ObjectiveScheduler().next_ready(project) is not None
    project_inputs = list_project_inputs(str(project.id))
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
    wants_json = (
        "application/json" in (request.headers.get("Accept") or "")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )
    if updated is None:
        if wants_json:
            return JsonResponse(
                {"ok": False, "error": f"Invalid status: {status or action or '(empty)'}"},
                status=400,
            )
        messages.error(request, f"Unknown triage action: {action or status or '(empty)'}")
    else:
        if wants_json:
            counts = project.findings.aggregate(
                all=Count("id"),
                open=Count("id", filter=Q(status=FindingStatus.OPEN)),
                confirmed=Count("id", filter=Q(status=FindingStatus.CONFIRMED)),
            )
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
    return redirect(f"{url}?finding_status={status_q}&finding_severity={severity_q}#findings")


@require_GET
def project_findings_json(request: HttpRequest, pk) -> JsonResponse:
    """Findings board rows for live filter / refresh without full page reload."""
    project = get_object_or_404(Project, pk=pk)
    status_f = (request.GET.get("finding_status") or "all").strip().lower()
    severity_f = (request.GET.get("finding_severity") or "all").strip().lower()
    rows = FindingStore().board(project, status=status_f, severity=severity_f)
    counts = project.findings.aggregate(
        all=Count("id"),
        open=Count("id", filter=Q(status=FindingStatus.OPEN)),
        confirmed=Count("id", filter=Q(status=FindingStatus.CONFIRMED)),
    )
    return JsonResponse(
        {
            "findings": FindingStore().board_payload(
                project, status=status_f, severity=severity_f
            ),
            "counts": counts,
            "statuses": list(FindingStatus.choices),
            "severities": list(FindingSeverity.choices),
        }
    )

@require_http_methods(["POST"])
def project_chat(request: HttpRequest, pk) -> HttpResponse:
    """Live-feed prompt → full project replan from the operator message."""
    project = get_object_or_404(Project, pk=pk)
    wants_json = (
        "application/json" in (request.headers.get("Accept") or "")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )
    if request.content_type and "application/json" in request.content_type:
        try:
            body = json.loads(request.body.decode() or "{}")
        except json.JSONDecodeError:
            body = {}
        message = (body.get("message") or "").strip()
    else:
        message = (request.POST.get("message") or "").strip()

    if not message:
        if wants_json:
            return JsonResponse({"ok": False, "error": "Message is required."}, status=400)
        messages.error(request, "Message is required.")
        return redirect("project_detail", pk=project.pk)

    if not llm_configured():
        err = "Set OPENROUTER_API_KEY (or OpenAI/LiteLLM) to replan from chat."
        if wants_json:
            return JsonResponse({"ok": False, "error": err}, status=503)
        messages.error(request, err)
        return redirect("project_detail", pk=project.pk)

    try:
        result = ProjectLifecycle.replan_from_prompt(project, message)
    except RuntimeError as exc:
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=409)
        messages.error(request, str(exc))
        return redirect("project_detail", pk=project.pk)
    except ValueError as exc:
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, str(exc))
        return redirect("project_detail", pk=project.pk)
    except Exception as exc:
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)
        messages.error(request, f"Replan failed: {exc}")
        return redirect("project_detail", pk=project.pk)

    if wants_json:
        return JsonResponse({"ok": True, **result})
    messages.success(
        request,
        f"Replanned — {result.get('objectives', 0)} objectives; "
        f"next job {result.get('primary_job_id') or '(none)'}",
    )
    return redirect("project_detail", pk=project.pk)


def _roe_add_path(project: Project, sandbox_path: str) -> None:
    roe, _ = RulesOfEngagement.objects.get_or_create(project=project)
    path = (sandbox_path or "").strip()
    if not path:
        return
    seed = coerce_targets(roe.seed)
    scope = coerce_targets(roe.in_scope)
    row = {"type": "path", "value": path}
    if not any(t.get("value") == path for t in seed):
        seed.append(row)
    if not any(t.get("value") == path for t in scope):
        scope.append(row)
    roe.seed = seed
    roe.in_scope = scope
    roe.save(update_fields=["seed", "in_scope", "updated_at"])


def _roe_remove_path(project: Project, sandbox_path: str) -> None:
    roe = getattr(project, "roe", None)
    if roe is None:
        return
    path = (sandbox_path or "").strip()
    seed = [t for t in coerce_targets(roe.seed) if t.get("value") != path]
    scope = [t for t in coerce_targets(roe.in_scope) if t.get("value") != path]
    roe.seed = seed
    roe.in_scope = scope
    roe.save(update_fields=["seed", "in_scope", "updated_at"])


@require_http_methods(["GET", "POST"])
def project_inputs(request: HttpRequest, pk) -> HttpResponse:
    """List / upload project-local input files (``workspace/inputs/`` only)."""
    project = get_object_or_404(Project, pk=pk)
    wants_json = (
        "application/json" in (request.headers.get("Accept") or "")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )
    if request.method == "GET":
        return JsonResponse({"ok": True, "inputs": list_project_inputs(str(project.id))})

    if project.status == ProjectStatus.CANCELLED:
        if wants_json:
            return JsonResponse(
                {"ok": False, "error": "Project cancelled — uploads disabled."},
                status=409,
            )
        messages.error(request, "Project cancelled — uploads disabled.")
        return redirect("project_detail", pk=project.pk)

    files = request.FILES.getlist("uploads") or request.FILES.getlist("file")
    if not files and request.FILES:
        files = list(request.FILES.values())
    if not files:
        if wants_json:
            return JsonResponse({"ok": False, "error": "No file uploaded."}, status=400)
        messages.error(request, "No file uploaded.")
        return redirect("project_detail", pk=project.pk)

    saved_rows: list[dict] = []
    errors: list[str] = []
    for f in files:
        try:
            row = save_project_input(str(project.id), f)
            _roe_add_path(project, row["sandbox_path"])
            saved_rows.append(row)
            # Surface in live stream on a recent/running job when possible.
            job = (
                project.jobs.filter(status=JobStatus.RUNNING)
                .order_by("-updated_at")
                .first()
                or project.jobs.order_by("-updated_at").first()
            )
            if job is not None:
                from peon.projects.streaming import record_stream_message

                record_stream_message(
                    str(job.id),
                    "log",
                    f"Uploaded {row['name']} → {row['sandbox_path']} (this project only)",
                    {
                        "event": "project_upload",
                        "role": "user",
                        "project_id": str(project.id),
                        "path": row["sandbox_path"],
                    },
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
    return redirect("project_detail", pk=project.pk)


@require_http_methods(["POST"])
def project_input_delete(request: HttpRequest, pk) -> HttpResponse:
    """Remove one file from this project's inputs/ tree."""
    project = get_object_or_404(Project, pk=pk)
    wants_json = (
        "application/json" in (request.headers.get("Accept") or "")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )
    if request.content_type and "application/json" in request.content_type:
        try:
            body = json.loads(request.body.decode() or "{}")
        except json.JSONDecodeError:
            body = {}
        name = (body.get("name") or "").strip()
    else:
        name = (request.POST.get("name") or "").strip()

    sandbox_path = f"/workspace/inputs/{Path(name).name}" if name else ""
    ok = delete_project_input(str(project.id), name) if name else False
    if ok:
        _roe_remove_path(project, sandbox_path)
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
    return redirect("project_detail", pk=project.pk)


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
        return redirect("project_detail", pk=project.pk)

    if action == "add_candidates":
        n = add_candidates(roe, parse_target_lines(request.POST.get("candidates") or ""))
        if n:
            messages.success(request, f"Added {n} candidate(s)")
        else:
            messages.warning(request, "No candidates parsed")
        return redirect("project_detail", pk=project.pk)

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
        messages.success(request, "RoE updated")
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
                "RoE deduced: " + ", ".join(format_targets(project.roe.in_scope)),
            )
        else:
            messages.warning(
                request,
                "RoE saved with empty in-scope — active probe skills need at least "
                "one target value (type labels like ip:/person: are optional hints).",
            )
    return redirect("project_detail", pk=project.pk)


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
        brief = (request.POST.get("description") or project.summary or project.title).strip()
        try:
            result = PlanningService.replan_project(project, description=brief)
            n = len(result.objectives or [])
            jid = result.job.id if result.job else "(none)"
            messages.success(
                request, f"Replanned — {n} objectives; next job {jid}"
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
    wants_json = (
        "application/json" in (request.headers.get("Accept") or "")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )
    if request.content_type and "application/json" in request.content_type:
        try:
            body = json.loads(request.body.decode() or "{}")
        except json.JSONDecodeError:
            body = {}
        command = (body.get("command") or body.get("description") or "").strip()
    else:
        command = (
            request.POST.get("command") or request.POST.get("description") or ""
        ).strip()

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
    return JsonResponse(ProjectStatusPayload.for_project(project, jobs_limit=PROJECT_JOBS_LIMIT))


@require_GET
def project_messages_json(request: HttpRequest, pk) -> JsonResponse:
    project = get_object_or_404(Project, pk=pk)
    after = request.GET.get("after") or "0"
    try:
        after_id = int(after)
    except (TypeError, ValueError):
        after_id = 0
    qs = (
        StreamMessage.objects.filter(job__project=project, id__gt=after_id)
        .select_related("job")
        .order_by("id")[:STREAM_POLL_LIMIT]
    )
    rows = []
    for msg in qs:
        msg._payload_title = msg.job.title  # type: ignore[attr-defined]
        msg._payload_status = msg.job.status  # type: ignore[attr-defined]
        msg._payload_project = str(project.id)  # type: ignore[attr-defined]
        rows.append(message_to_dict(msg))
    return JsonResponse({"messages": rows})


@require_GET
def job_live_json(request: HttpRequest, pk, job_id) -> JsonResponse:
    project = get_object_or_404(Project, pk=pk)
    job = get_object_or_404(Job, pk=job_id, project=project)
    return JsonResponse({**ProjectStatusPayload.job_payload(job), "result": job.result})


@require_GET
def search_json(request: HttpRequest) -> JsonResponse:
    """Operator Search (⇧S): projects, findings, skills, tools, settings, jumps."""
    q = (request.GET.get("q") or "").strip()
    q_lower = q.lower()
    items: list[dict] = [
        {
            "id": "nav-projects",
            "label": "Projects",
            "group": "Navigate",
            "href": reverse("project_list"),
            "keywords": "home list engagements",
        },
        {
            "id": "nav-project-new",
            "label": "New project",
            "group": "Navigate",
            "href": reverse("project_create"),
            "keywords": "create start engagement",
        },
        {
            "id": "nav-catalog",
            "label": "Catalog",
            "group": "Navigate",
            "href": reverse("catalog"),
            "keywords": "skills tools",
        },
        {
            "id": "nav-learn",
            "label": "Learn",
            "group": "Navigate",
            "href": reverse("learn"),
            "keywords": "author skill tool yaml suggest writer",
        },
        {
            "id": "nav-catalog-skills",
            "label": "Skills catalog",
            "group": "Navigate",
            "href": reverse("catalog") + "#skills",
            "keywords": "skill registry",
        },
        {
            "id": "nav-catalog-tools",
            "label": "Tools catalog",
            "group": "Navigate",
            "href": reverse("catalog") + "#tools",
            "keywords": "cli sandbox provision",
        },
        {
            "id": "nav-settings",
            "label": "Settings",
            "group": "Navigate",
            "href": reverse("settings_page"),
            "keywords": "caps threads parallel agent dramatiq policy",
        },
        {
            "id": "nav-admin",
            "label": "Admin",
            "group": "Navigate",
            "href": "/admin/",
            "keywords": "django",
        },
    ]
    # Settings field keywords (so Shift+S search finds knobs by name).
    for field in PeonSettings.field_meta():
        items.append(
            {
                "id": f"setting-{field['key']}",
                "label": field["label"],
                "group": "Settings",
                "href": reverse("settings_page") + f"#{field['key']}",
                "keywords": f"{field['key']} {field['help']} setting",
                "meta": str(field["value"]),
            }
        )

    projects = Project.objects.all()
    if q:
        projects = projects.filter(
            Q(title__icontains=q) | Q(summary__icontains=q) | Q(status__icontains=q)
        )
    for p in projects[:PALETTE_PROJECT_LIMIT]:
        items.append(
            {
                "id": f"project-{p.id}",
                "label": p.title,
                "group": "Projects",
                "href": reverse("project_detail", kwargs={"pk": p.pk}),
                "keywords": f"{p.status} {p.id} {p.summary or ''}",
                "meta": p.status,
            }
        )

    findings = Finding.objects.select_related("project").all()
    if q:
        findings = findings.filter(
            Q(title__icontains=q)
            | Q(description__icontains=q)
            | Q(host__icontains=q)
            | Q(kind__icontains=q)
            | Q(cve_id__icontains=q)
            | Q(project__title__icontains=q)
        )
    for f in findings.order_by("-updated_at")[:PALETTE_FINDING_LIMIT]:
        items.append(
            {
                "id": f"finding-{f.id}",
                "label": f"FIND-{f.seq}: {f.title}",
                "group": "Findings",
                "href": reverse("project_detail", kwargs={"pk": f.project_id})
                + "#findings",
                "keywords": f"{f.kind} {f.severity} {f.host} {f.cve_id} {f.project.title}",
                "meta": f"{f.severity} · {f.project.title}",
            }
        )

    catalog_base = reverse("catalog") + "?all=1"
    skill_hits = 0
    for skill in SkillCards.catalog(jobable_only=False):
        hay = " ".join(
            [
                skill.get("name") or "",
                skill.get("category") or "",
                skill.get("description") or "",
                " ".join(skill.get("tags") or []),
                " ".join(skill.get("aliases") or []),
                "skill",
            ]
        ).lower()
        if q_lower and q_lower not in hay:
            continue
        name = skill["name"]
        items.append(
            {
                "id": f"skill-{name}",
                "label": name,
                "group": "Skills",
                "href": f"{catalog_base}#skill-{name}",
                "keywords": hay,
                "meta": skill.get("category") or ("required" if skill.get("required") else ""),
            }
        )
        skill_hits += 1
        if skill_hits >= PALETTE_SKILL_LIMIT:
            break

    tool_hits = 0
    for tool in ToolCards.catalog():
        hay = " ".join(
            [
                tool.get("id") or "",
                tool.get("name") or "",
                tool.get("description") or "",
                tool.get("binary") or "",
                tool.get("tier") or "",
                " ".join(tool.get("tags") or []),
                " ".join(tool.get("binaries") or []),
                " ".join(tool.get("skills") or []),
                "tool cli",
            ]
        ).lower()
        if q_lower and q_lower not in hay:
            continue
        tid = tool["id"]
        items.append(
            {
                "id": f"tool-{tid}",
                "label": tid,
                "group": "Tools",
                "href": f"{catalog_base}#tool-{tid}",
                "keywords": hay,
                "meta": tool.get("tier") or tool.get("binary") or "",
            }
        )
        tool_hits += 1
        if tool_hits >= PALETTE_TOOL_LIMIT:
            break

    running = (
        Job.objects.filter(status=JobStatus.RUNNING)
        .select_related("project")
        .order_by("-updated_at")[:20]
    )
    for job in running:
        if job.project_id is None:
            continue
        items.append(
            {
                "id": f"job-{job.id}",
                "label": f"{job.title} (running)",
                "group": "Active runs",
                "href": reverse(
                    "job_live", kwargs={"pk": job.project_id, "job_id": job.pk}
                ),
                "keywords": f"live {job.project.title if job.project else ''}",
                "meta": "running",
            }
        )
    return JsonResponse({"items": items, "q": q})


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

# --- URLconf ---

urlpatterns = [
    path("", project_list, name="project_list"),
    path("projects/new/", project_create, name="project_create"),
    path("settings/", settings_page, name="settings_page"),
    path("search.json", search_json, name="search_json"),
    path("projects/bulk/", projects_bulk, name="projects_bulk"),
    path("projects/<uuid:pk>/", project_detail, name="project_detail"),
    path(
        "projects/<uuid:pk>/findings/<uuid:finding_id>/triage/",
        project_finding_triage,
        name="project_finding_triage",
    ),
    path(
        "projects/<uuid:pk>/findings.json",
        project_findings_json,
        name="project_findings_json",
    ),
    path(
        "projects/<uuid:pk>/reports/<path:file_path>/view/",
        project_report_view,
        name="project_report_view",
    ),
    path(
        "projects/<uuid:pk>/reports/<path:file_path>",
        project_report_file,
        name="project_report_file",
    ),
    path("projects/<uuid:pk>/control/", project_control, name="project_control"),
    path("projects/<uuid:pk>/chat/", project_chat, name="project_chat"),
    path("projects/<uuid:pk>/inputs/", project_inputs, name="project_inputs"),
    path(
        "projects/<uuid:pk>/inputs/delete/",
        project_input_delete,
        name="project_input_delete",
    ),
    path("projects/<uuid:pk>/jobs/bulk/", jobs_bulk, name="jobs_bulk"),
    path("projects/<uuid:pk>/roe/", project_roe, name="project_roe"),
    path("projects/<uuid:pk>/jobs.json", project_jobs_json, name="project_jobs_json"),
    path(
        "projects/<uuid:pk>/messages.json",
        project_messages_json,
        name="project_messages_json",
    ),
    path("projects/<uuid:pk>/jobs/<uuid:job_id>/start/", job_start, name="job_start"),
    path("projects/<uuid:pk>/jobs/<uuid:job_id>/remove/", job_remove, name="job_remove"),
    path("projects/<uuid:pk>/jobs/<uuid:job_id>/", job_live, name="job_live"),
    path(
        "projects/<uuid:pk>/jobs/<uuid:job_id>/live.json",
        job_live_json,
        name="job_live_json",
    ),
    path(
        "projects/<uuid:pk>/jobs/<uuid:job_id>/steer/",
        job_steer,
        name="job_steer",
    ),
]
