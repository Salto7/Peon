# Skill policies fold into CatalogSkillRelation.conditions — drop the separate model.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0043_skill_relation_kinds_policies"),
    ]

    operations = [
        migrations.DeleteModel(
            name="CatalogSkillPolicy",
        ),
    ]
