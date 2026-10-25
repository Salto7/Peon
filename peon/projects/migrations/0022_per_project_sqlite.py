import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0021_normalize_job_workspace_ids"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="running_jobs",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="project",
            name="failed_jobs",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="project",
            name="open_prompts",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="project",
            name="last_error_summary",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.CreateModel(
            name="RoleSkillMemory",
            fields=[
                (
                    "role_id",
                    models.CharField(max_length=128, primary_key=True, serialize=False),
                ),
                ("skills", models.JSONField(blank=True, default=list)),
                ("counts", models.JSONField(blank=True, default=dict)),
                (
                    "last_source",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "last_project_id",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Role skill memory",
                "verbose_name_plural": "Role skill memory",
            },
        ),
        migrations.AlterField(
            model_name="rulesofengagement",
            name="project",
            field=models.OneToOneField(
                db_constraint=False,
                on_delete=django.db.models.deletion.CASCADE,
                primary_key=True,
                related_name="+",
                serialize=False,
                to="projects.project",
            ),
        ),
        migrations.AlterField(
            model_name="objective",
            name="project",
            field=models.ForeignKey(
                db_constraint=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="projects.project",
            ),
        ),
        migrations.AlterField(
            model_name="job",
            name="project",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="projects.project",
            ),
        ),
        migrations.AlterField(
            model_name="operatorprompt",
            name="project",
            field=models.ForeignKey(
                db_constraint=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="projects.project",
            ),
        ),
        migrations.AlterField(
            model_name="projectschedule",
            name="project",
            field=models.ForeignKey(
                db_constraint=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="projects.project",
            ),
        ),
        migrations.AlterField(
            model_name="finding",
            name="project",
            field=models.ForeignKey(
                db_constraint=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="projects.project",
            ),
        ),
        migrations.AlterField(
            model_name="assetgraph",
            name="project",
            field=models.OneToOneField(
                db_constraint=False,
                on_delete=django.db.models.deletion.CASCADE,
                primary_key=True,
                related_name="+",
                serialize=False,
                to="projects.project",
            ),
        ),
        migrations.AlterField(
            model_name="jobcheckpoint",
            name="project",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="projects.project",
            ),
        ),
        migrations.AlterField(
            model_name="agentmessage",
            name="project",
            field=models.ForeignKey(
                db_constraint=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="projects.project",
            ),
        ),
    ]
