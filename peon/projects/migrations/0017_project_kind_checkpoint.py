import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0016_finding_subjects"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="kind",
            field=models.CharField(
                choices=[("timed", "Timed"), ("durable", "Durable")],
                default="timed",
                help_text="timed = finite engagement; durable = 24/7 continuous project.",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="project",
            name="plan_accepted",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "False until operator accepts the durable plan graph "
                    "(timed defaults True)."
                ),
            ),
        ),
        migrations.CreateModel(
            name="JobCheckpoint",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("seq", models.PositiveIntegerField(default=1)),
                (
                    "reason",
                    models.CharField(
                        blank=True,
                        default="job_finished",
                        help_text="job_finished | resume | steer | durable_tick",
                        max_length=64,
                    ),
                ),
                (
                    "context",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text=(
                            "Structured domain context: status, roles, "
                            "digest refs, artifacts."
                        ),
                    ),
                ),
                ("transcript_digest", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "job",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="checkpoints",
                        to="projects.job",
                    ),
                ),
                (
                    "objective",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="checkpoints",
                        to="projects.objective",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="checkpoints",
                        to="projects.project",
                    ),
                ),
            ],
            options={
                "ordering": ["job", "-seq", "-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="jobcheckpoint",
            index=models.Index(fields=["job", "-seq"], name="projects_jo_job_id_ckpt_idx"),
        ),
        migrations.AddIndex(
            model_name="jobcheckpoint",
            index=models.Index(
                fields=["project", "-created_at"],
                name="projects_jo_project_ckpt_idx",
            ),
        ),
    ]
