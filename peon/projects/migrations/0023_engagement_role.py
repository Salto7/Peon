from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0022_per_project_sqlite"),
    ]

    operations = [
        migrations.CreateModel(
            name="EngagementRole",
            fields=[
                (
                    "role_id",
                    models.CharField(max_length=128, primary_key=True, serialize=False),
                ),
                (
                    "kind",
                    models.CharField(
                        help_text="engagement-pm | analyzer | technical",
                        max_length=32,
                    ),
                ),
                ("aliases", models.JSONField(blank=True, default=list)),
                (
                    "protected",
                    models.BooleanField(
                        default=False,
                        help_text="Bookend roles that Lab must not delete.",
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Engagement role",
                "verbose_name_plural": "Engagement roles",
                "ordering": ["role_id"],
            },
        ),
    ]
