from django.db import migrations


def normalize_project_job_workspaces(apps, schema_editor):
    Job = apps.get_model("projects", "Job")
    for job in Job.objects.exclude(project_id__isnull=True).iterator():
        expected = str(job.project_id)
        if job.workspace_id != expected:
            Job.objects.filter(pk=job.pk).update(workspace_id=expected)


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0020_alter_job_role_ids_alter_jobcheckpoint_reason_and_more"),
    ]

    operations = [
        migrations.RunPython(
            normalize_project_job_workspaces,
            migrations.RunPython.noop,
        ),
    ]
