# Expanded skill relation kinds, optional conditions, and CatalogSkillPolicy.

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0042_workflow_lint_errors_dict"),
    ]

    operations = [
        migrations.AlterField(
            model_name="catalogskillrelation",
            name="kind",
            field=models.CharField(
                choices=[
                    ("sequence", "Runs after"),
                    ("depends_on", "Depends on"),
                    ("preferred_over", "Preferred over"),
                    ("supersedes", "Supersedes"),
                    ("does_not_run_with", "Does not run with"),
                    ("runs_under", "Runs under"),
                    ("overlaps", "Overlaps with"),
                ],
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="catalogskillrelation",
            name="conditions",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Optional — when this relation applies. Empty = always.",
            ),
        ),
        migrations.CreateModel(
            name="CatalogSkillPolicy",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("skill_id", models.CharField(max_length=128, unique=True)),
                (
                    "conditions",
                    models.TextField(
                        blank=True,
                        default="",
                        help_text="Optional — when to use this skill. Empty = always.",
                    ),
                ),
                ("note", models.CharField(blank=True, default="", max_length=500)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name_plural": "Catalog skill policies",
                "ordering": ["skill_id"],
            },
        ),
    ]
