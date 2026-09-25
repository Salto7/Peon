"""Project / console URL routes."""

from django.urls import path

from peon.projects import terminal_views as term_views
from peon.projects import views
from peon.projects.search_palette import search_json

urlpatterns = [
    path("", views.home, name="home"),
    path("projects/", views.project_list, name="project_list"),
    path("projects/new/", views.project_create, name="project_create"),
    path("settings/", views.settings_page, name="settings_page"),
    path("search.json", search_json, name="search_json"),
    path("projects/bulk/", views.projects_bulk, name="projects_bulk"),
    path("projects/<uuid:pk>/", views.project_detail, name="project_detail"),
    path(
        "projects/<uuid:pk>/findings/<uuid:finding_id>/triage/",
        views.project_finding_triage,
        name="project_finding_triage",
    ),
    path(
        "projects/<uuid:pk>/findings.json",
        views.project_findings_json,
        name="project_findings_json",
    ),
    path(
        "projects/<uuid:pk>/reports/<path:file_path>/view/",
        views.project_report_view,
        name="project_report_view",
    ),
    path(
        "projects/<uuid:pk>/reports/<path:file_path>",
        views.project_report_file,
        name="project_report_file",
    ),
    path("projects/<uuid:pk>/control/", views.project_control, name="project_control"),
    path("projects/<uuid:pk>/chat/", views.project_chat, name="project_chat"),
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
    path("projects/<uuid:pk>/inputs/", views.project_inputs, name="project_inputs"),
    path(
        "projects/<uuid:pk>/inputs/delete/",
        views.project_input_delete,
        name="project_input_delete",
    ),
    path("projects/<uuid:pk>/jobs/bulk/", views.jobs_bulk, name="jobs_bulk"),
    path("projects/<uuid:pk>/roe/", views.project_roe, name="project_roe"),
    path("projects/<uuid:pk>/jobs.json", views.project_jobs_json, name="project_jobs_json"),
    path(
        "projects/<uuid:pk>/messages.json",
        views.project_messages_json,
        name="project_messages_json",
    ),
    path("projects/<uuid:pk>/jobs/<uuid:job_id>/start/", views.job_start, name="job_start"),
    path("projects/<uuid:pk>/jobs/<uuid:job_id>/remove/", views.job_remove, name="job_remove"),
    path("projects/<uuid:pk>/jobs/<uuid:job_id>/", views.job_live, name="job_live"),
    path(
        "projects/<uuid:pk>/jobs/<uuid:job_id>/live.json",
        views.job_live_json,
        name="job_live_json",
    ),
    path(
        "projects/<uuid:pk>/jobs/<uuid:job_id>/steer/",
        views.job_steer,
        name="job_steer",
    ),
]
