"""Create a plan and persist Project/Job/Objective rows (no execution)."""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from peon.projects.targets import parse_target_lines
from peon.projects.services import PlanningService


def _csv(raw: str | None) -> list[str]:
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


class Command(BaseCommand):
    help = (
        "Run ProjectPlanner/JobPlanner (orchestrator library), save plan to disk "
        "and create DB rows. Does not execute."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("brief", nargs="?", default="", help="Job / project brief")
        parser.add_argument("--brief-file", dest="brief_file", default="")
        parser.add_argument("--mode", choices=("project", "job"), default="project")
        parser.add_argument("--title", default="adhoc")
        parser.add_argument("--summary", default="")
        parser.add_argument("--in-scope", dest="in_scope", default="")
        parser.add_argument("--exclusions", default="")
        parser.add_argument("--authorization", default="")
        parser.add_argument("--skills", default="", help="Comma-separated skill ids")

    def handle(self, *args, **options) -> None:
        description = (options.get("brief") or "").strip()
        brief_file = (options.get("brief_file") or "").strip()
        if brief_file:
            description = Path(brief_file).read_text(encoding="utf-8").strip()
        if not description:
            raise CommandError("Provide a brief string or --brief-file")

        try:
            result = PlanningService.run_llm_plan(
                description=description,
                mode=options["mode"],
                title=options["title"],
                summary=options["summary"],
                in_scope=parse_target_lines(options["in_scope"]),
                exclusions=parse_target_lines(options["exclusions"]),
                authorization=options["authorization"],
                skills=_csv(options["skills"]),
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(result.plan_text)
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Job {result.job.id} workspace={result.job.workspace_id} "
                f"plan={result.job.plan_path}"
            )
        )
        if result.project is not None:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Project {result.project.id} objectives={len(result.objectives or [])}"
                )
            )
