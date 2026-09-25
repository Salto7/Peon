"""Shared job helpers (stream anchors, live checks)."""

from __future__ import annotations

from peon.projects.models import Job, JobStatus, Project

_LIVE = frozenset({JobStatus.RUNNING, JobStatus.PENDING, JobStatus.PAUSED})


def anchor_job(project: Project, *, prefer_live: bool = True) -> Job | None:
    """Pick a job to hang stream/audit lines on.

    When ``prefer_live`` is True, prefer a live root job; otherwise (and as
    fallback) the most recently updated root, then any job.
    """
    roots = project.jobs.filter(parent__isnull=True)
    if prefer_live:
        live = roots.filter(status__in=_LIVE).order_by("-updated_at").first()
        if live is not None:
            return live
    return (
        roots.order_by("-updated_at").first()
        or project.jobs.order_by("-updated_at").first()
    )


def has_live_agents(project: Project) -> bool:
    return project.jobs.filter(status__in=_LIVE).exists()
