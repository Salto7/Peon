"""Project / console URL routes."""

from django.urls import path

from peon.projects import project_control_views as control
from peon.projects import project_pages as pages
from peon.projects import terminal_views as term_views
from peon.projects.search_palette import search_json

urlpatterns = [
    path("", pages.home, name="home"),
    path("projects/", pages.project_list, name="project_list"),
    path("projects/new/", pages.project_create, name="project_create"),
    path("settings/", pages.settings_page, name="settings_page"),
    path("search.json", search_json, name="search_json"),
    path("projects/bulk/", control.projects_bulk, name="projects_bulk"),
    path("projects/<uuid:pk>/", pages.project_detail, name="project_detail"),
    path(
        "projects/<uuid:pk>/findings/<uuid:finding_id>/triage/",
        control.project_finding_triage,
        name="project_finding_triage",
    ),
    path(
        "projects/<uuid:pk>/findings.json",
        control.project_findings_json,
        name="project_findings_json",
    ),
    path(
        "projects/<uuid:pk>/reports/<path:file_path>/view/",
        pages.project_report_view,
        name="project_report_view",
    ),
    path(
        "projects/<uuid:pk>/reports/<path:file_path>",
        pages.project_report_file,
        name="project_report_file",
    ),
    path("projects/<uuid:pk>/control/", control.project_control, name="project_control"),
    path("projects/<uuid:pk>/chat/", control.project_chat, name="project_chat"),
    path(
        "projects/<uuid:pk>/terminal/",
        term_views.project_terminal_open,
        name="project_terminal_open",
    ),
    path(
        "projects/<uuid:pk>/terminal/<str:session_id>/stream/",
        term_views.project_terminal_stream,
        name="project_terminal_stream",
    ),
    path(
        "projects/<uuid:pk>/terminal/<str:session_id>/input/",
        term_views.project_terminal_input,
        name="project_terminal_input",
    ),
    path(
        "projects/<uuid:pk>/terminal/<str:session_id>/resize/",
        term_views.project_terminal_resize,
        name="project_terminal_resize",
    ),
    path(
        "projects/<uuid:pk>/terminal/<str:session_id>/close/",
        term_views.project_terminal_close,
        name="project_terminal_close",
    ),
    path("projects/<uuid:pk>/inputs/", control.project_inputs, name="project_inputs"),
    path(
        "projects/<uuid:pk>/inputs/delete/",
        control.project_input_delete,
        name="project_input_delete",
    ),
    path("projects/<uuid:pk>/jobs/bulk/", control.jobs_bulk, name="jobs_bulk"),
    path("projects/<uuid:pk>/roe/", control.project_roe, name="project_roe"),
    path("projects/<uuid:pk>/jobs.json", control.project_jobs_json, name="project_jobs_json"),
    path(
        "projects/<uuid:pk>/messages.json",
        control.project_messages_json,
        name="project_messages_json",
    ),
    path("projects/<uuid:pk>/jobs/<uuid:job_id>/start/", control.job_start, name="job_start"),
    path(
        "projects/<uuid:pk>/jobs/<uuid:job_id>/remove/", control.job_remove, name="job_remove"
    ),
    path("projects/<uuid:pk>/jobs/<uuid:job_id>/", pages.job_live, name="job_live"),
    path(
        "projects/<uuid:pk>/jobs/<uuid:job_id>/live.json",
        control.job_live_json,
        name="job_live_json",
    ),
    path(
        "projects/<uuid:pk>/jobs/<uuid:job_id>/steer/",
        control.job_steer,
        name="job_steer",
    ),
]
