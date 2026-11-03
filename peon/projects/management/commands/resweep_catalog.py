"""Re-run role resolution for every skill/MCP currently unbound from a role."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from peon.projects.platform_catalog import PlatformCatalog


class Command(BaseCommand):
    help = (
        "Resolve every skill/MCP with zero role bindings (same mechanism a "
        "role create/edit triggers automatically) — a one-off catch-up run, "
        "e.g. right after removing the old peon.affinity.yaml sidecar files."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=200,
            help="Max orphans to resolve in one run (default: 200).",
        )

    def handle(self, *args, **options):
        limit = int(options.get("limit") or 200)
        result = PlatformCatalog.shared().resweep_unbound_owners(limit=limit)
        self.stdout.write(
            f"scanned {result['scanned']}, resolved {result['resolved']}, "
            f"remaining {result['remaining']}"
        )
        if result["remaining"]:
            self.stdout.write(
                self.style.WARNING(
                    "remaining orphans exist — re-run (or raise --limit) to continue"
                )
            )
