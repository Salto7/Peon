"""Lint skills under SKILLS_DIR (provision layer)."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from orchestrator.skills.provision import SkillLinter


class Command(BaseCommand):
    help = "Validate SKILL.md catalog (orchestrator provision lint)"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--path", help="Skills root (default: SKILLS_DIR)")

    def handle(self, *args, **options) -> None:
        root = Path(options["path"]) if options["path"] else Path(settings.SKILLS_DIR)
        results = SkillLinter.lint_root(root.resolve())
        verbose = int(options.get("verbosity") or 1) >= 2
        errors = 0
        for result in results:
            issues = result.get("issues") or []
            bad = [i for i in issues if i.get("level") == "error"]
            warn = [i for i in issues if i.get("level") == "warning"]
            name = result.get("skill_name") or result.get("skill_dir")
            if bad:
                errors += 1
                self.stdout.write(self.style.ERROR(f"FAIL {name}"))
                for i in bad:
                    self.stdout.write(f"  error[{i.get('code')}]: {i.get('message')}")
            elif verbose:
                extra = f" ({len(warn)} warnings)" if warn else ""
                self.stdout.write(f"OK   {name}{extra}")
            if verbose or bad:
                for i in warn:
                    self.stdout.write(f"  warn[{i.get('code')}]: {i.get('message')}")
        if not results:
            self.stderr.write(f"No skills found under {root}")
            raise SystemExit(1)
        self.stdout.write(f"{len(results) - errors}/{len(results)} skills ok")
        if errors:
            raise SystemExit(1)
