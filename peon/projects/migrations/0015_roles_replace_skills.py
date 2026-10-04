from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0014_project_crew_flow"),
    ]

    operations = [
        migrations.RenameField(
            model_name="job",
            old_name="skill_names",
            new_name="role_ids",
        ),
        migrations.RenameField(
            model_name="objective",
            old_name="skill_suggestion",
            new_name="role_id",
        ),
    ]
