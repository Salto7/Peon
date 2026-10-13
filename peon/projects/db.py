"""Per-project SQLite databases + platform registry routing."""

from __future__ import annotations

import logging
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from django.conf import settings
from django.db import connections

logger = logging.getLogger(__name__)

_local = threading.local()

# Platform-only (never created on project_* aliases).
PLATFORM_ONLY_MODEL_NAMES = frozenset(
    {
        "RuntimeSettings",
        "RoleSkillMemory",
        "EngagementRole",
        "CatalogSkill",
        "CatalogFile",
        "CatalogBinding",
        "CatalogMcp",
    }
)

# Registry row: authoritative on default; mirrored into each project DB so
# engagement FKs (job.project) resolve without cross-DB joins.
REGISTRY_MODEL_NAMES = frozenset({"Project"})

PLATFORM_MODEL_NAMES = PLATFORM_ONLY_MODEL_NAMES | REGISTRY_MODEL_NAMES

# Engagement tables live in /workspaces/<uuid>/db.sqlite3 (and orphan Jobs on default).
PROJECT_MODEL_NAMES = frozenset(
    {
        "RulesOfEngagement",
        "Objective",
        "Job",
        "JobDirective",
        "OperatorPrompt",
        "ProjectSchedule",
        "StreamMessage",
        "Finding",
        "AssetGraph",
        "JobCheckpoint",
        "AgentMessage",
        "ProjectMcpConfig",
    }
)


def normalize_project_id(project_id: object) -> str:
    try:
        return str(uuid.UUID(str(project_id)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"invalid project id {project_id!r}") from exc


def alias_for(project_id: object) -> str:
    return f"project_{normalize_project_id(project_id).replace('-', '')}"


def project_db_path(project_id: object) -> Path:
    from peon.projects.workspaces import project_workspace_dir

    root = project_workspace_dir(normalize_project_id(project_id), create=True)
    return (root / "db.sqlite3").resolve()


def get_project_context() -> str:
    return str(getattr(_local, "project_id", "") or "")


def set_project_context(project_id: object | None) -> str:
    previous = get_project_context()
    if project_id is None or project_id == "":
        _local.project_id = ""
    else:
        _local.project_id = normalize_project_id(project_id)
    return previous


@contextmanager
def using_project(project_id: object | None) -> Iterator[str]:
    """Bind the current thread to a project DB alias (empty = platform/default)."""
    previous = set_project_context(project_id)
    try:
        if project_id is None or project_id == "":
            yield "default"
        else:
            pid = normalize_project_id(project_id)
            ensure_project_database(pid)
            yield alias_for(pid)
    finally:
        set_project_context(previous or None)


def _sqlite_config(name: Path) -> dict[str, Any]:
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(name),
        "ATOMIC_REQUESTS": False,
        "AUTOCOMMIT": True,
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": False,
        # busy_timeout (seconds) — project DBs are hit by web + worker + HITL poll.
        "OPTIONS": {"timeout": 30},
        "TIME_ZONE": None,
        "USER": "",
        "PASSWORD": "",
        "HOST": "",
        "PORT": "",
        "TEST": {
            "CHARSET": None,
            "COLLATION": None,
            "NAME": None,
            "MIRROR": None,
        },
    }


_SQLITE_PRAGMA_READY = False


def configure_sqlite_concurrency() -> None:
    """Enable WAL + busy_timeout on every SQLite connection (idempotent)."""
    global _SQLITE_PRAGMA_READY
    if _SQLITE_PRAGMA_READY:
        return
    from django.db.backends.signals import connection_created

    def _on_connection_created(sender, connection, **kwargs) -> None:
        del sender, kwargs
        if connection.vendor != "sqlite":
            return
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            # Milliseconds — complements OPTIONS timeout on the Python driver.
            cursor.execute("PRAGMA busy_timeout=30000;")

    connection_created.connect(_on_connection_created, dispatch_uid="peon.sqlite.wal")
    _SQLITE_PRAGMA_READY = True


# Aliases that have already had migrations applied this process.
_migrated_aliases: set[str] = set()
_migrate_lock = threading.Lock()


def register_project_database(project_id: object) -> str:
    """Ensure settings.DATABASES + connections know the project alias."""
    pid = normalize_project_id(project_id)
    alias = alias_for(pid)
    path = project_db_path(pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    databases = settings.DATABASES
    cfg = _sqlite_config(path)
    existing = databases.get(alias)
    if existing is None or str(existing.get("NAME") or "") != str(path):
        databases[alias] = cfg
        # Close any stale handle so Django rebuilds from settings.DATABASES.
        if alias in connections:
            try:
                connections[alias].close()
            except Exception:
                pass
            try:
                del connections[alias]
            except Exception:
                pass
    return alias


def mirror_project_row(project_id: object) -> None:
    """Copy the platform Project registry row into the project SQLite file."""
    from peon.projects.models import Project

    pid = normalize_project_id(project_id)
    alias = register_project_database(pid)
    src = Project.objects.using("default").filter(pk=pid).first()
    if src is None:
        return
    defaults = {
        f.attname: getattr(src, f.attname)
        for f in Project._meta.concrete_fields
        if f.attname != "id"
    }
    Project.objects.using(alias).update_or_create(pk=pid, defaults=defaults)


def ensure_project_database(project_id: object) -> str:
    """Register alias and apply migrations once per process.

    The Project registry row is mirrored into the project file **only** when the
    alias is first prepared here, and again from ``Project.save`` on registry
    edits. Remirroring on every ensure was a write storm (claim slot checks,
    stream fan-out, finish→enqueue) that held the SQLite writer lock and aborted
    objective dispatch with ``database is locked``.
    """
    pid = normalize_project_id(project_id)
    alias = register_project_database(pid)
    from django.core.management import call_command

    with _migrate_lock:
        if alias not in _migrated_aliases:
            try:
                call_command(
                    "migrate",
                    "projects",
                    database=alias,
                    run_syncdb=False,
                    verbosity=0,
                    interactive=False,
                )
            except Exception:
                logger.exception(
                    "project db migrate failed project_id=%s alias=%s", pid, alias
                )
                raise
            try:
                mirror_project_row(pid)
            except Exception:
                logger.exception("project mirror failed project_id=%s", pid)
                raise
            _migrated_aliases.add(alias)
    return alias


def drop_project_database(project_id: object) -> None:
    """Close and unregister a project DB alias (file removal is caller's job)."""
    try:
        pid = normalize_project_id(project_id)
    except ValueError:
        return
    alias = alias_for(pid)
    if alias in connections:
        connections[alias].close()
        del connections[alias]
    settings.DATABASES.pop(alias, None)


def project_alias_or_default(project_id: object | None) -> str:
    if not project_id:
        return "default"
    return alias_for(project_id)


def qs_using(model: type, project_id: object | None):
    """Queryset for a model on the correct DB for this project (or platform)."""
    alias = project_alias_or_default(project_id)
    if alias != "default":
        ensure_project_database(project_id)
    return model.objects.using(alias)


def get_operator_prompt(prompt_id: object, *, unresolved_only: bool = True):
    """Load an OperatorPrompt from any project DB (or raise DoesNotExist)."""
    from peon.projects.models import OperatorPrompt, Project

    pid = str(prompt_id)
    qs_filter = {"pk": pid}
    if unresolved_only:
        qs_filter["resolved_at__isnull"] = True
    try:
        return OperatorPrompt.objects.using("default").get(**qs_filter)
    except OperatorPrompt.DoesNotExist:
        pass
    for project_id in Project.objects.values_list("id", flat=True):
        alias = ensure_project_database(project_id)
        try:
            return OperatorPrompt.objects.using(alias).get(**qs_filter)
        except OperatorPrompt.DoesNotExist:
            continue
    raise OperatorPrompt.DoesNotExist(f"OperatorPrompt matching query does not exist: {pid}")


def get_job(job_id: object, *, project_id: object | None = None):
    """Load a Job from its project DB (or platform orphans / fan-out)."""
    from peon.projects.models import Job, Project

    jid = str(job_id)
    if project_id:
        alias = ensure_project_database(project_id)
        return Job.objects.using(alias).get(pk=jid)

    # Prefer thread context
    ctx = get_project_context()
    if ctx:
        try:
            return Job.objects.using(ensure_project_database(ctx)).get(pk=jid)
        except Job.DoesNotExist:
            pass

    try:
        return Job.objects.using("default").get(pk=jid)
    except Job.DoesNotExist:
        pass

    for pid in Project.objects.values_list("id", flat=True):
        alias = ensure_project_database(pid)
        try:
            return Job.objects.using(alias).get(pk=jid)
        except Job.DoesNotExist:
            continue
    raise Job.DoesNotExist(f"Job matching query does not exist: {jid}")


def iter_project_aliases():
    """Yield (project_id, alias) for every registry Project."""
    from peon.projects.models import Project

    for pid in Project.objects.values_list("id", flat=True):
        yield str(pid), ensure_project_database(pid)


class ProjectRelation:
    """Related-manager stand-in that always hits the project SQLite file."""

    def __init__(self, model: type, project: Any, *, field: str = "project") -> None:
        self.model = model
        self.project = project
        self.field = field
        self.alias = ensure_project_database(project.pk)

    def _base(self):
        return self.model.objects.using(self.alias).filter(**{self.field: self.project})

    def all(self):
        return self._base()

    def filter(self, *args, **kwargs):
        return self._base().filter(*args, **kwargs)

    def exclude(self, *args, **kwargs):
        return self._base().exclude(*args, **kwargs)

    def get(self, *args, **kwargs):
        return self._base().get(*args, **kwargs)

    def create(self, **kwargs):
        kwargs.setdefault(self.field, self.project)
        return self.model.objects.using(self.alias).create(**kwargs)

    def get_or_create(self, defaults=None, **kwargs):
        kwargs.setdefault(self.field, self.project)
        return self.model.objects.using(self.alias).get_or_create(
            defaults=defaults, **kwargs
        )

    def update_or_create(self, defaults=None, **kwargs):
        kwargs.setdefault(self.field, self.project)
        return self.model.objects.using(self.alias).update_or_create(
            defaults=defaults, **kwargs
        )

    def count(self) -> int:
        return self._base().count()

    def exists(self) -> bool:
        return self._base().exists()

    def order_by(self, *fields):
        return self._base().order_by(*fields)

    def select_related(self, *fields):
        return self._base().select_related(*fields)

    def prefetch_related(self, *lookups):
        return self._base().prefetch_related(*lookups)

    def values_list(self, *fields, **kwargs):
        return self._base().values_list(*fields, **kwargs)

    def values(self, *fields, **kwargs):
        return self._base().values(*fields, **kwargs)

    def aggregate(self, *args, **kwargs):
        return self._base().aggregate(*args, **kwargs)

    def annotate(self, *args, **kwargs):
        return self._base().annotate(*args, **kwargs)

    def first(self):
        return self._base().first()

    def last(self):
        return self._base().last()

    def iterator(self, *args, **kwargs):
        return self._base().iterator(*args, **kwargs)

    def __iter__(self):
        return iter(self._base())

    def __getitem__(self, item):
        return self._base().__getitem__(item)


def refresh_project_kpis(project_id: object) -> None:
    """Update denormalized Project counters from the project DB (no cross-DB join)."""
    from peon.projects.models import (
        Job,
        JobStatus,
        OperatorPrompt,
        Project,
    )

    pid = normalize_project_id(project_id)
    alias = ensure_project_database(pid)
    running = Job.objects.using(alias).filter(
        project_id=pid, status=JobStatus.RUNNING
    ).count()
    failed = Job.objects.using(alias).filter(
        project_id=pid, status=JobStatus.FAILED
    ).count()
    open_prompts = OperatorPrompt.objects.using(alias).filter(
        project_id=pid, resolved_at__isnull=True
    ).count()
    last_error = (
        Job.objects.using(alias)
        .filter(project_id=pid, status=JobStatus.FAILED)
        .exclude(error="")
        .order_by("-updated_at")
        .values_list("error", flat=True)
        .first()
    ) or ""
    Project.objects.filter(pk=pid).update(
        running_jobs=running,
        failed_jobs=failed,
        open_prompts=open_prompts,
        last_error_summary=(last_error or "")[:500],
    )


class PeonDatabaseRouter:
    """Platform models → default; engagement models → project_* or default orphans."""

    def _model_name(self, model: type) -> str:
        return model._meta.object_name

    def _is_projects(self, model: type) -> bool:
        return model._meta.app_label == "projects"

    def db_for_read(self, model, **hints):
        return self._route(model, **hints)

    def db_for_write(self, model, **hints):
        return self._route(model, **hints)

    def _route(self, model, **hints) -> str | None:
        if not self._is_projects(model):
            return "default"
        name = self._model_name(model)
        if name in PLATFORM_ONLY_MODEL_NAMES:
            return "default"
        if name in REGISTRY_MODEL_NAMES:
            # Authoritative registry reads/writes use default unless an instance
            # already lives on a project alias (mirror).
            instance = hints.get("instance")
            if instance is not None and getattr(instance._state, "db", None):
                db = instance._state.db
                if db and str(db).startswith("project_"):
                    return db
            return "default"

        # Engagement models + auto M2M through tables (e.g. Objective_depends_on).
        instance = hints.get("instance")
        project_id = hints.get("project_id")
        if project_id:
            return alias_for(project_id)
        if instance is not None:
            # Prefer the instance's existing DB (covers M2M through writes).
            db = getattr(instance._state, "db", None)
            if db and str(db).startswith("project_"):
                return db
            pid = getattr(instance, "project_id", None)
            if pid:
                return alias_for(pid)
            # Never touch instance.job (FK fetch re-enters this router).
            # Cached related object or raw job_id only.
            fields = getattr(instance, "_state", None)
            cached = getattr(fields, "fields_cache", None) or {}
            job = cached.get("job")
            if job is not None and getattr(job, "project_id", None):
                return alias_for(job.project_id)
            job_id = getattr(instance, "job_id", None)
            if job_id:
                ctx = get_project_context()
                if ctx:
                    return alias_for(ctx)

        ctx = get_project_context()
        if ctx:
            return alias_for(ctx)
        if name in PROJECT_MODEL_NAMES:
            return "default"
        # Unknown projects models (through tables) without hints → default
        return "default"

    def allow_relation(self, obj1, obj2, **hints):
        if obj1._meta.app_label == "projects" and obj2._meta.app_label == "projects":
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label != "projects":
            return db == "default"
        if model_name is None:
            return True
        from django.apps import apps

        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            return db == "default"
        name = model._meta.object_name
        if name in PLATFORM_ONLY_MODEL_NAMES:
            return db == "default"
        # Registry, engagement tables, and M2M through tables on both.
        return db == "default" or str(db).startswith("project_")
