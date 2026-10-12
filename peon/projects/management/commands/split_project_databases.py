"""Move engagement rows from the platform DB into per-project SQLite files."""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connections, transaction

from peon.projects.db import (
    PROJECT_MODEL_NAMES,
    ensure_project_database,
    using_project,
)
from peon.projects.models import Project


class Command(BaseCommand):
    help = (
        "For each Project registry row, ensure /workspaces/<id>/db.sqlite3 and "
        "copy engagement tables out of the platform DB."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Copy rows and delete them from platform (default dry-run).",
        )

    def handle(self, *args, **options) -> None:
        persist = bool(options.get("apply"))
        projects = list(Project.objects.using("default").all())
        self.stdout.write(f"projects={len(projects)} persist={persist}")
        for project in projects:
            pid = str(project.id)
            alias = ensure_project_database(pid)
            counts = self._copy_project(project, alias, persist=persist)
            self.stdout.write(f"- {pid} {project.title}: {counts}")

        if not persist:
            self.stdout.write(self.style.WARNING("dry-run: platform rows not deleted"))

    def _copy_project(self, project: Project, alias: str, *, persist: bool) -> dict:
        from peon.projects import models as m

        pid = project.pk
        order = [
            "RulesOfEngagement",
            "Objective",
            "Job",
            "JobDirective",
            "StreamMessage",
            "JobCheckpoint",
            "Finding",
            "AssetGraph",
            "OperatorPrompt",
            "ProjectSchedule",
            "AgentMessage",
        ]
        counts: dict[str, int] = {}
        with using_project(pid):
            job_ids = list(
                m.Job.objects.using("default")
                .filter(project_id=pid)
                .values_list("pk", flat=True)
            )
            for name in order:
                if name not in PROJECT_MODEL_NAMES or not hasattr(m, name):
                    continue
                model = getattr(m, name)
                if name in {"JobDirective", "StreamMessage"}:
                    src = model.objects.using("default").filter(job_id__in=job_ids)
                elif name == "JobCheckpoint":
                    src = model.objects.using("default").filter(project_id=pid)
                else:
                    src = model.objects.using("default").filter(project_id=pid)

                rows = list(src)
                counts[name] = len(rows)
                if not persist or not rows:
                    continue

                with transaction.atomic(using=alias):
                    if name == "Objective":
                        for row in rows:
                            self._upsert(model, alias, row)
                        for row in rows:
                            dest = model.objects.using(alias).get(pk=row.pk)
                            dest.depends_on.set(
                                list(
                                    row.depends_on.using("default").values_list(
                                        "pk", flat=True
                                    )
                                )
                            )
                    else:
                        for row in rows:
                            self._upsert(model, alias, row)

                src.delete()
        return counts

    def _upsert(self, model, alias: str, row, *, skip_m2m: bool = False) -> None:
        fields = {}
        for field in model._meta.concrete_fields:
            if field.primary_key:
                continue
            fields[field.attname] = getattr(row, field.attname)
        model.objects.using(alias).update_or_create(pk=row.pk, defaults=fields)
