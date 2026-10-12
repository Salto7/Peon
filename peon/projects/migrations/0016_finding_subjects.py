from django.db import migrations, models


def backfill_subjects(apps, schema_editor):
    Finding = apps.get_model("projects", "Finding")
    for finding in Finding.objects.all().iterator():
        subjects: list[dict] = []
        host = (finding.host or "").strip()
        if host:
            typ = (finding.asset_type or "").strip() or "host"
            subjects.append({"type": typ, "value": host, "relation": "affected"})
            if finding.port is not None:
                try:
                    port_i = int(finding.port)
                except (TypeError, ValueError):
                    port_i = None
                if port_i is not None and 1 <= port_i <= 65535:
                    subjects.append(
                        {
                            "type": "service",
                            "value": f"{host}:{port_i}",
                            "relation": "affected",
                        }
                    )
        meta = finding.metadata if isinstance(finding.metadata, dict) else {}
        for key in ("subjects", "assets", "discovered_assets"):
            raw = meta.get(key)
            if not isinstance(raw, list):
                continue
            for item in raw:
                if isinstance(item, dict):
                    typ = str(item.get("type") or item.get("asset_type") or "").strip()
                    value = str(item.get("value") or item.get("target") or "").strip()
                    if typ and value:
                        subjects.append(
                            {"type": typ, "value": value, "relation": "affected"}
                        )
                elif isinstance(item, str) and item.strip():
                    subjects.append(
                        {
                            "type": (finding.asset_type or "other").strip() or "other",
                            "value": item.strip(),
                            "relation": "affected",
                        }
                    )
        # Dedupe by type:value
        seen: set[str] = set()
        uniq: list[dict] = []
        for row in subjects:
            key = f"{row['type'].lower()}:{row['value'].lower()}"
            if key in seen:
                continue
            seen.add(key)
            uniq.append(row)
        Finding.objects.filter(pk=finding.pk).update(subjects=uniq)


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0015_roles_replace_skills"),
    ]

    operations = [
        migrations.AddField(
            model_name="finding",
            name="subjects",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    'Affected subjects: [{"type","value"}] (optional "relation"). '
                    "evidence_path is never a subject."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="finding",
            name="evidence_path",
            field=models.CharField(
                blank=True,
                help_text="Workspace provenance path only — not an engagement subject.",
                max_length=1024,
            ),
        ),
        migrations.RunPython(backfill_subjects, migrations.RunPython.noop),
    ]
