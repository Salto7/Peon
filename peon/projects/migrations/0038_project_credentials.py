import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0037_catalogbinding_tools"),
    ]

    operations = [
        migrations.AlterField(
            model_name="project",
            name="kind",
            field=models.CharField(
                choices=[
                    ("timed", "Timed"),
                    ("durable", "Durable"),
                    ("assume_breach", "Assume breach"),
                ],
                default="timed",
                help_text=(
                    "timed = finite engagement; durable = 24/7 continuous project; "
                    "assume_breach = timed, operator-defined objectives, starts from "
                    "supplied credentials."
                ),
                max_length=16,
            ),
        ),
        migrations.CreateModel(
            name="ProjectCredential",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("name", models.CharField(max_length=128)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("password", "Password"),
                            ("ntlm_hash", "NTLM hash"),
                            ("kerberos_ticket", "Kerberos ticket"),
                            ("ssh_key", "SSH key"),
                            ("api_token", "API token"),
                            ("certificate", "Certificate"),
                            ("other", "Other"),
                        ],
                        default="password",
                        max_length=24,
                    ),
                ),
                ("username", models.CharField(blank=True, default="", max_length=255)),
                ("domain", models.CharField(blank=True, default="", max_length=255)),
                ("secret_enc", models.TextField(blank=True, default="")),
                ("notes", models.TextField(blank=True, default="")),
                ("source", models.CharField(blank=True, default="provided", max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "project",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="projects.project",
                    ),
                ),
            ],
            options={
                "verbose_name": "Project credential",
                "ordering": ["name"],
            },
        ),
        migrations.AddConstraint(
            model_name="projectcredential",
            constraint=models.UniqueConstraint(
                fields=("project", "name"), name="project_credential_unique_name"
            ),
        ),
    ]
