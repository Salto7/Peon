"""Rewrite legacy RuntimeSettings keys once (previously patched on every read)."""

from django.db import migrations

_RETIRED = ("COGNEE_API_BASE", "HELPERS_DIR")


def forwards(apps, schema_editor):
    RuntimeSettings = apps.get_model("projects", "RuntimeSettings")
    for row in RuntimeSettings.objects.all():
        values = dict(row.values) if isinstance(row.values, dict) else {}
        if "DRAMATIQ_THREADS" in values:
            values.setdefault("DRAMATIQ_MAX_THREADS", values["DRAMATIQ_THREADS"])
            del values["DRAMATIQ_THREADS"]
        if "PEON_DREAMING_INTERVAL_MS" in values:
            try:
                ms = int(values.pop("PEON_DREAMING_INTERVAL_MS") or 900_000)
            except (TypeError, ValueError):
                ms = 900_000
            values.setdefault("PEON_DREAMING_INTERVAL_S", max(60, ms // 1000))
        for key in _RETIRED:
            values.pop(key, None)
        if values != row.values:
            row.values = values
            row.save(update_fields=["values"])


class Migration(migrations.Migration):
    dependencies = [("projects", "0027_engagement_role_text")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
