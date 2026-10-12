from __future__ import annotations

from django.core.management.base import BaseCommand

from peon.projects.resource_cleanup import ProjectResourcePruner


class Command(BaseCommand):
    help = "Find or remove workspaces and sandboxes with no owning Project."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Delete orphan resources (default is dry-run).",
        )

    def handle(self, *args, **options) -> None:
        persist = bool(options.get("apply"))
        result = ProjectResourcePruner.run(persist=persist)
        workspaces = result["workspaces"]
        self.stdout.write(
            f"orphan projects={len(result['orphan_project_ids'])}; "
            f"workspace entries={len(workspaces['orphans'])}; "
            f"removed={len(workspaces['removed'])}; "
            f"errors={len(workspaces['errors'])}"
        )
        for project_id in result["orphan_project_ids"]:
            self.stdout.write(f"- {project_id}")
        if not persist:
            self.stdout.write(self.style.WARNING("dry-run: no resources removed"))
