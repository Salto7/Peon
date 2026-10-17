from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0031_memory_entries"),
    ]

    operations = [
        migrations.AddField(
            model_name="catalogmcp",
            name="reference_hosts",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
