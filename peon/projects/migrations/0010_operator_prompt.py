# Generated manually for OperatorPrompt (HITL console)

from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0009_job_directive"),
    ]

    operations = [
        migrations.CreateModel(
            name="OperatorPrompt",
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
                ("question", models.TextField()),
                ("options", models.JSONField(blank=True, default=list)),
                ("reply", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                (
                    "job",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="operator_prompts",
                        to="projects.job",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="operator_prompts",
                        to="projects.project",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="operatorprompt",
            index=models.Index(
                fields=["project", "resolved_at"],
                name="projects_op_project_7c1a2b_idx",
            ),
        ),
    ]
