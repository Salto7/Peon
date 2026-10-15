from django.db import migrations, models


def fill_role_text(apps, schema_editor):
    del schema_editor
    Role = apps.get_model("projects", "EngagementRole")
    from orchestrator.crew.roles.catalog import seed_role_text

    for row in Role.objects.all():
        goal, backstory = seed_role_text(row.role_id, row.kind)
        changed = []
        if not (row.goal or "").strip():
            row.goal = goal
            changed.append("goal")
        if not (row.backstory or "").strip():
            row.backstory = backstory
            changed.append("backstory")
        if changed:
            row.save(update_fields=changed)


def seed_catalog(apps, schema_editor):
    del apps, schema_editor
    from peon.projects.platform_catalog import PlatformCatalog

    PlatformCatalog.shared().ensure_seeded()


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0026_catalog_mcp_cwd"),
    ]

    operations = [
        migrations.AddField(
            model_name="engagementrole",
            name="goal",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="engagementrole",
            name="backstory",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.RunPython(fill_role_text, migrations.RunPython.noop),
        migrations.RunPython(seed_catalog, migrations.RunPython.noop),
    ]
