# Generated manually for StreamMessage

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0002_job_operator_note"),
    ]

    operations = [
        migrations.CreateModel(
            name="StreamMessage",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "message_type",
                    models.CharField(
                        choices=[
                            ("log", "Log"),
                            ("stdout", "Stdout"),
                            ("stderr", "Stderr"),
                            ("tool", "Tool"),
                            ("thinking", "Thinking"),
                            ("result", "Result"),
                            ("error", "Error"),
                            ("status", "Status"),
                        ],
                        default="log",
                        max_length=20,
                    ),
                ),
                ("content", models.TextField()),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "job",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="messages",
                        to="projects.job",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="streammessage",
            index=models.Index(fields=["job", "created_at"], name="projects_st_job_id_7a1c0f_idx"),
        ),
    ]
