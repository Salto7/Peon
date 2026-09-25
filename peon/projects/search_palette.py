"""Operator Search (⇧S) palette JSON."""

from __future__ import annotations

from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_GET

from peon.projects.catalog import SkillCards, ToolCards
from peon.projects.models import Finding, Job, JobStatus, Project
from peon.projects.runtime_settings import PeonSettings

PALETTE_PROJECT_LIMIT = 40
PALETTE_FINDING_LIMIT = 40
PALETTE_SKILL_LIMIT = 40
PALETTE_TOOL_LIMIT = 40


@require_GET
def search_json(request: HttpRequest) -> JsonResponse:
    """Operator Search (⇧S): projects, findings, skills, tools, settings, jumps."""
    q = (request.GET.get("q") or "").strip()
    q_lower = q.lower()
    items: list[dict] = [
        {
            "id": "nav-home",
            "label": "Home",
            "group": "Navigate",
            "href": reverse("home"),
            "keywords": "dashboard ops status kpis",
        },
        {
            "id": "nav-projects",
            "label": "Projects",
            "group": "Navigate",
            "href": reverse("project_list"),
            "keywords": "list engagements",
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
            "label": "Shared catalog",
            "group": "Navigate",
            "href": reverse("catalog"),
            "keywords": "skills tools",
        },
        {
            "id": "nav-learn",
            "label": "Toolsmith",
            "group": "Navigate",
            "href": reverse("learn"),
            "keywords": "learn lab author skill tool yaml suggest writer forge",
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
            "label": "Peon Settings",
            "group": "Navigate",
            "href": reverse("settings_page"),
            "keywords": "caps threads parallel agent dramatiq policy theme dark light",
        },
        {
            "id": "setting-theme",
            "label": "Theme (dark / light)",
            "group": "Settings",
            "href": reverse("settings_page") + "#appearance",
            "keywords": "appearance theme dark light mode ui",
            "meta": "local",
        },
        {
            "id": "nav-admin",
            "label": "Django admin",
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
