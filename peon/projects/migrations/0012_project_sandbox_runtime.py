from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0011_asset_graph"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="sandbox_runtime",
            field=models.CharField(
                choices=[("sandbox", "Sandbox"), ("openshell", "OpenShell")],
                default="sandbox",
                help_text="Chosen at project create. Sandbox is Docker; OpenShell is the locked runtime.",
                max_length=32,
            ),
        ),
    ]
