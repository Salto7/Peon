import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0024_alter_role_help_text"),
    ]

    operations = [
        migrations.CreateModel(
            name="CatalogSkill",
            fields=[
                ("skill_id", models.CharField(max_length=128, primary_key=True, serialize=False)),
                ("provider", models.CharField(default="peon", max_length=32)),
                ("description", models.TextField(blank=True, default="")),
                ("body", models.TextField(blank=True, default="")),
                ("manifest", models.JSONField(blank=True, default=dict)),
                ("setup", models.JSONField(blank=True, null=True)),
                ("content_hash", models.CharField(blank=True, default="", max_length=64)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["skill_id"]},
        ),
        migrations.CreateModel(
            name="CatalogMcp",
            fields=[
                ("mcp_id", models.CharField(max_length=128, primary_key=True, serialize=False)),
                ("description", models.TextField(blank=True, default="")),
                ("transport", models.CharField(default="stdio", max_length=16)),
                ("command", models.CharField(max_length=256)),
                ("args", models.JSONField(blank=True, default=list)),
                ("install", models.JSONField(blank=True, null=True)),
                ("env_schema", models.JSONField(blank=True, default=list)),
                ("enabled", models.BooleanField(default=True)),
                ("content_hash", models.CharField(blank=True, default="", max_length=64)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["mcp_id"]},
        ),
        migrations.CreateModel(
            name="CatalogFile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("owner_kind", models.CharField(max_length=16)),
                ("owner_id", models.CharField(max_length=128)),
                ("path", models.CharField(max_length=512)),
                ("body", models.TextField(blank=True, default="")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="CatalogBinding",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("role_id", models.CharField(max_length=128)),
                ("owner_kind", models.CharField(max_length=16)),
                ("owner_id", models.CharField(max_length=128)),
                ("score", models.FloatField(default=1.0)),
                ("lead", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="ProjectMcpConfig",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("mcp_id", models.CharField(max_length=128)),
                ("env", models.JSONField(blank=True, default=dict)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "project",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="projects.project",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="catalogfile",
            constraint=models.UniqueConstraint(
                fields=("owner_kind", "owner_id", "path"),
                name="catalog_file_owner_path",
            ),
        ),
        migrations.AddIndex(
            model_name="catalogfile",
            index=models.Index(fields=["owner_kind", "owner_id"], name="projects_ca_owner_k_15a764_idx"),
        ),
        migrations.AddConstraint(
            model_name="catalogbinding",
            constraint=models.UniqueConstraint(
                fields=("role_id", "owner_kind", "owner_id"),
                name="catalog_binding_role_owner",
            ),
        ),
        migrations.AddIndex(
            model_name="catalogbinding",
            index=models.Index(fields=["owner_kind", "owner_id"], name="projects_ca_owner_k_251582_idx"),
        ),
        migrations.AddIndex(
            model_name="catalogbinding",
            index=models.Index(fields=["role_id"], name="projects_ca_role_id_0eaf01_idx"),
        ),
        migrations.AddConstraint(
            model_name="projectmcpconfig",
            constraint=models.UniqueConstraint(
                fields=("project", "mcp_id"),
                name="project_mcp_config_unique",
            ),
        ),
    ]
