"""List filesystem skills (SkillRegistry)."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from orchestrator.skills.misc.catalog import filter_skills, format_catalog
from orchestrator.skills.misc.registry import SkillRegistry


class Command(BaseCommand):
    help = "List skills from SKILLS_DIR via SkillRegistry"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--all", action="store_true", help="Include non-jobable")

    def handle(self, *args, **options) -> None:
        if options["json"]:
            skills = SkillRegistry.shared().discover_skills()
            if not options["all"]:
                skills = [s for s in skills if s.get("jobable", True)]
            self.stdout.write(json.dumps(skills, indent=2, default=str))
            return
        self.stdout.write(
            format_catalog(
                filter_skills(
                    SkillRegistry.shared().get_registry().values(),
                    jobable_only=not options["all"],
                ),
                mode="discover",
                header="Available skills (activate to load full instructions):",
            )
        )
