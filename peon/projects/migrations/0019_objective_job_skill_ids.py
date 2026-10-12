from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0018_operator_prompt_kind_project_schedule"),
    ]

    operations = [
        migrations.AddField(
            model_name="objective",
            name="skill_ids",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Selected skill ids from the role's skill group (subset).",
            ),
        ),
        migrations.AddField(
            model_name="job",
            name="skill_ids",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Selected skill ids for this job (subset of the role skill group).",
            ),
        ),
    ]
