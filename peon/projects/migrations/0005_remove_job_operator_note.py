# Generated manually for remove Job.operator_note

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0004_project_status_paused"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="job",
            name="operator_note",
        ),
    ]
