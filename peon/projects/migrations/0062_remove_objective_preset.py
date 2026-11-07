"""Drop unused ObjectivePreset.

no views/URLs/frontend ever referenced it. Historical 0055 kept.
.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0061_freeze_engagement_roles"),
    ]

    operations = [
        migrations.DeleteModel(name="ObjectivePreset"),
    ]
