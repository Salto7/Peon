"""One set of project controls for the project page and the projects list."""

from __future__ import annotations

from django import template
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

from peon.projects.models import ProjectStatus

register = template.Library()

_RUNNABLE = ProjectStatus.ACTIVE
_PAUSED = ProjectStatus.PAUSED
_STOPPABLE = {ProjectStatus.ACTIVE, ProjectStatus.PAUSED}


def _items(project, *, bulk: bool) -> list[dict]:
    status = getattr(project, "status", "") if project is not None else ""
    title = getattr(project, "title", "") or "this project"

    def enabled(action: str) -> bool:
        if bulk:
            return True
        if action == "pause":
            return status == _RUNNABLE
        if action == "resume":
            return status == _PAUSED
        if action == "stop":
            return status in _STOPPABLE
        return True

    if bulk:
        stop_confirm = (
            "In-flight agents on the selected projects are cancelled and those "
            "projects are marked stopped."
        )
        restart_confirm = (
            "In-flight agents on the selected projects are cancelled and each "
            "project manager is queued again."
        )
        delete_confirm = (
            "The selected projects and their Docker sandboxes will be permanently removed."
        )
        stop_title = "Stop selected projects?"
        restart_title = "Restart selected projects?"
        delete_title = "Delete selected projects?"
    else:
        stop_confirm = "In-flight agents are cancelled and the project is marked stopped."
        restart_confirm = "In-flight agents are cancelled and the project manager is queued again."
        delete_confirm = f"“{title}” and its Docker sandbox will be permanently removed."
        stop_title = "Stop project?"
        restart_title = "Restart project?"
        delete_title = "Delete project?"

    rows = (
        ("stop", "Stop", "square", stop_confirm, stop_title, False),
        ("pause", "Pause", "pause", "", "", False),
        ("restart", "Restart", "rotate-ccw", restart_confirm, restart_title, False),
        ("resume", "Resume", "play", "", "", False),
        ("delete", "Delete", "trash-2", delete_confirm, delete_title, True),
    )
    return [
        {
            "action": action,
            "label": label,
            "icon": icon,
            "confirm": confirm,
            "confirm_title": confirm_title,
            "destructive": destructive,
            "disabled": not enabled(action),
            "sep_after": action == "resume",
        }
        for action, label, icon, confirm, confirm_title, destructive in rows
    ]


@register.simple_tag(takes_context=True)
def project_actions(context, project=None, mode: str = "toolbar", next: str = ""):
    """Render stop, pause, restart, resume and delete for one project or a bulk bar."""
    html = render_to_string(
        "includes/project_actions.html",
        {
            "actions": _items(project, bulk=mode == "bulk"),
            "mode": mode,
            "project": project,
            "next": next,
        },
        request=context.get("request"),
    )
    return mark_safe(html)
