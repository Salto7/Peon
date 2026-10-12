"""Canonicalize source subjects and rebuild attack-surface graphs."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from peon.projects.graph_repair import repair_all_projects, repair_project_graph
from peon.projects.models import Project


class Command(BaseCommand):
    help = "Canonicalize finding subjects, remove legacy pollution, and rebuild graphs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--project",
            dest="project_id",
            default="",
            help="Optional project UUID (default: all projects).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute removals without saving.",
        )

    def handle(self, *args, **options):
        persist = not bool(options.get("dry_run"))
        project_id = str(options.get("project_id") or "").strip()
        if project_id:
            project = Project.objects.get(pk=project_id)
            rows = [repair_project_graph(project, persist=persist)]
        else:
            rows = repair_all_projects(persist=persist)
        for row in rows:
            self.stdout.write(
                f"{row['project_id']}: removed "
                f"{len(row['removed_candidates'])} candidates, "
                f"{len(row['removed_assets'])} assets; "
                f"graph={row['node_count']} nodes / {row['edge_count']} edges; "
                f"duplicates={row['duplicate_identities']}, "
                f"orphan-services={row['orphan_services']}, "
                f"dangling-edges={row['dangling_edges']}"
            )
        if not persist:
            self.stdout.write(self.style.WARNING("dry-run: no changes saved"))
