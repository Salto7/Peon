# Generated manually for RuntimeSettings

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0007_finding_freeform_labels"),
    ]

    operations = [
        migrations.CreateModel(
            name="RuntimeSettings",
            fields=[
                (
                    "id",
                    models.PositiveSmallIntegerField(
                        default=1, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("values", models.JSONField(blank=True, default=dict)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Runtime settings",
                "verbose_name_plural": "Runtime settings",
            },
        ),
    ]
