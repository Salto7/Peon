"""Lab page for installing imported Claude Agent Skills."""

from __future__ import annotations

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import path
from django.views.decorators.http import require_GET, require_POST

from orchestrator.skills.install.dragdrop import install_relative_files, install_zip
from orchestrator.skills.install.openskills import install_openskills
from orchestrator.skills.providers.base import CLAUDE_PROVIDER
from orchestrator.skills.registry import SkillRegistry

_MAX_BYTES = 20 * 1024 * 1024


def _packs() -> list:
    return [
        doc
        for doc in SkillRegistry.shared().list_skills()
        if doc.provider == CLAUDE_PROVIDER
    ]


@require_GET
def lab_skills_page(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "lab/skills.html",
        {
            "nav": "lab",
            "lab_section": "skills",
            "packs": _packs(),
        },
    )


@require_POST
def lab_skills_install(request: HttpRequest) -> HttpResponse:
    source = str(request.POST.get("source") or "").strip()
    try:
        names = install_openskills(source)
    except (RuntimeError, ValueError, OSError) as exc:
        messages.error(request, str(exc))
        return redirect("lab_skills")
    messages.success(request, "Installed " + ", ".join(names))
    return redirect("lab_skills")


@require_POST
def lab_skills_upload(request: HttpRequest) -> HttpResponse:
    uploads = request.FILES.getlist("file")
    paths = [str(item) for item in request.POST.getlist("path")]
    if not uploads:
        messages.error(request, "Drop a skill folder or zip.")
        return redirect("lab_skills")
    total = sum(int(item.size or 0) for item in uploads)
    if total > _MAX_BYTES:
        messages.error(request, "Upload is larger than 20 MB.")
        return redirect("lab_skills")
    try:
        zip_name = uploads[0].name.lower()
        zip_path = paths[0].lower() if paths else ""
        if len(uploads) == 1 and (zip_name.endswith(".zip") or zip_path.endswith(".zip")):
            names = install_zip(uploads[0].read(), filename=uploads[0].name or zip_path)
        else:
            if len(paths) != len(uploads):
                raise ValueError("each uploaded file needs a relative path")
            pairs = [
                (rel, upload.read())
                for rel, upload in zip(paths, uploads, strict=True)
            ]
            names = install_relative_files(pairs)
    except (RuntimeError, ValueError, OSError) as exc:
        messages.error(request, str(exc))
        return redirect("lab_skills")
    messages.success(request, "Installed " + ", ".join(names))
    return redirect("lab_skills")


urlpatterns = [
    path("", lab_skills_page, name="lab_skills"),
    path("install/", lab_skills_install, name="lab_skills_install"),
    path("upload/", lab_skills_upload, name="lab_skills_upload"),
]
