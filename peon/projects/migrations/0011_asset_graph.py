# Generated for AssetGraph (OAM-inspired open asset / relation store)

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0010_operator_prompt"),
    ]

    operations = [
        migrations.CreateModel(
            name="AssetGraph",
            fields=[
                (
                    "project",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="asset_graph",
                        serialize=False,
                        to="projects.project",
                    ),
                ),
                (
                    "assets",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text=(
                            'Assets: [{"id","type","value","key","bucket","source","props"?}]. '
                            "type is a free-form slug."
                        ),
                    ),
                ),
                (
                    "relations",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text=(
                            'Relations: [{"id","rel","source","target","props"?}]. '
                            "rel is a free-form slug."
                        ),
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
