# Generated manually for JobDirective

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0008_runtime_settings"),
    ]

    operations = [
        migrations.CreateModel(
            name="JobDirective",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "kind",
                    models.CharField(
                        choices=[("steer", "Steer"), ("followup", "Follow-up")],
                        default="steer",
                        max_length=16,
                    ),
                ),
                ("content", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "job",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="directives",
                        to="projects.job",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="jobdirective",
            index=models.Index(
                fields=["job", "consumed_at"], name="projects_jo_job_id_c0f1a2_idx"
            ),
        ),
    ]
