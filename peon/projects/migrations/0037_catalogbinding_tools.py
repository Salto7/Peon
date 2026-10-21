from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0036_marketplace_source"),
    ]

    operations = [
        migrations.AddField(
            model_name="catalogbinding",
            name="tools",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
