"""Platform-DB engagement role store (Lab-editable)."""

from __future__ import annotations

import logging
import time
from typing import Any

from django.db import IntegrityError, transaction

from orchestrator.crew.roles.catalog import (
    BOOKEND_KINDS,
    DEFAULT_ROLES,
    ROLE_KINDS,
    RoleDef,
    normalize_role_id,
    role_def_from_mapping,
)

logger = logging.getLogger(__name__)

# Bumped on every write so RoleRegistry notices changes within the same second.
_revision: float = 0.0


def _bump() -> float:
    global _revision
    _revision = max(_revision + 0.001, time.time())
    return _revision


def catalog_mtime() -> float:
    """Stamp for RoleRegistry cache invalidation (DB + local revision)."""
    try:
        from peon.projects.models import EngagementRole

        latest = (
            EngagementRole.objects.order_by("-updated_at")
            .values_list("updated_at", flat=True)
            .first()
        )
        db_m = float(latest.timestamp()) if latest is not None else 0.0
    except Exception:
        db_m = 0.0
    return max(db_m, _revision)


def _row_to_def(row) -> RoleDef:
    return RoleDef(
        id=str(row.role_id),
        kind=str(row.kind),
        aliases=tuple(str(a) for a in (row.aliases or []) if str(a).strip()),
        protected=bool(row.protected),
    )


def _invalidate_registry() -> None:
    _bump()
    try:
        from orchestrator.crew.roles.registry import RoleRegistry

        RoleRegistry.shared().invalidate()
    except Exception:
        pass


def ensure_seeded() -> int:
    """Insert DEFAULT_ROLES when the platform table is empty."""
    try:
        from peon.projects.models import EngagementRole
    except Exception as exc:
        logger.debug("role store unavailable: %s", exc)
        return 0
    if EngagementRole.objects.exists():
        return 0
    n = 0
    try:
        with transaction.atomic():
            for role in DEFAULT_ROLES:
                _, created = EngagementRole.objects.get_or_create(
                    role_id=role.id,
                    defaults={
                        "kind": role.kind,
                        "aliases": list(role.aliases),
                        "protected": bool(role.protected),
                    },
                )
                if created:
                    n += 1
    except IntegrityError:
        # Concurrent seed — table already populated.
        return 0
    if n:
        _invalidate_registry()
    return n


def list_role_defs() -> tuple[RoleDef, ...]:
    try:
        from peon.projects.models import EngagementRole

        ensure_seeded()
        rows = EngagementRole.objects.all().order_by("role_id")
        return tuple(_row_to_def(row) for row in rows)
    except Exception as exc:
        logger.debug("role store list failed: %s", exc)
        return DEFAULT_ROLES


def get_role_def(role_id: str) -> RoleDef | None:
    rid = normalize_role_id(role_id)
    if not rid:
        return None
    try:
        from peon.projects.models import EngagementRole

        ensure_seeded()
        row = EngagementRole.objects.filter(role_id=rid).first()
        if row is not None:
            return _row_to_def(row)
        for other in EngagementRole.objects.all().iterator():
            aliases = other.aliases or []
            if rid in {str(a).strip().lower() for a in aliases if str(a).strip()}:
                return _row_to_def(other)
        return None
    except Exception:
        for row in DEFAULT_ROLES:
            if row.id == rid or rid in row.aliases:
                return row
        return None


def upsert_role(
    *,
    role_id: str,
    kind: str,
    aliases: list[str] | tuple[str, ...] | str = (),
    protected: bool | None = None,
) -> RoleDef:
    from peon.projects.models import EngagementRole

    defn = role_def_from_mapping(
        {
            "id": role_id,
            "kind": kind,
            "aliases": aliases,
            "protected": bool(protected) if protected is not None else False,
        }
    )
    existing = EngagementRole.objects.filter(role_id=defn.id).first()
    if existing is not None:
        current = _row_to_def(existing)
        # Protected / bookend roles keep their kind (Lab UI disables the select).
        if current.is_protected and defn.kind != current.kind:
            raise ValueError(
                f"role {defn.id!r} is protected; kind cannot change from "
                f"{current.kind!r}"
            )
        keep_protected = current.protected
    else:
        keep_protected = defn.protected
        current = None

    if protected is not None:
        keep_protected = bool(protected)
    # Bookend kinds stay protected even if caller omits the flag.
    if defn.kind in BOOKEND_KINDS:
        keep_protected = True

    # At most one engagement-pm and one analyzer.
    if defn.kind in BOOKEND_KINDS:
        clash = (
            EngagementRole.objects.filter(kind=defn.kind)
            .exclude(role_id=defn.id)
            .first()
        )
        if clash is not None:
            raise ValueError(
                f"kind {defn.kind!r} already used by role {clash.role_id!r}"
            )

    ensure_seeded()
    for other in EngagementRole.objects.all().iterator():
        if other.role_id == defn.id:
            continue
        other_aliases = tuple(
            str(a).strip().lower() for a in (other.aliases or []) if str(a).strip()
        )
        conflict = {other.role_id, *other_aliases} & {defn.id, *defn.aliases}
        if conflict:
            raise ValueError(
                f"alias/id conflict with role {other.role_id!r}: {sorted(conflict)}"
            )

    final_kind = current.kind if (current and current.is_protected) else defn.kind
    EngagementRole.objects.update_or_create(
        role_id=defn.id,
        defaults={
            "kind": final_kind,
            "aliases": list(defn.aliases),
            "protected": keep_protected,
        },
    )
    _invalidate_registry()
    return RoleDef(
        id=defn.id,
        kind=final_kind,
        aliases=defn.aliases,
        protected=keep_protected,
    )


def delete_role(role_id: str) -> None:
    from peon.projects.models import EngagementRole

    rid = normalize_role_id(role_id)
    if not rid:
        raise ValueError("invalid role id")
    row = EngagementRole.objects.filter(role_id=rid).first()
    if row is None:
        raise KeyError(f"unknown role {rid!r}")
    defn = _row_to_def(row)
    if defn.is_protected:
        raise ValueError(f"role {rid!r} is protected and cannot be deleted")
    row.delete()
    _invalidate_registry()


def roles_for_lab() -> list[dict[str, Any]]:
    from orchestrator.crew.roles.registry import RoleRegistry

    registry = RoleRegistry.shared()
    out: list[dict[str, Any]] = []
    for defn in list_role_defs():
        spec = registry.get(defn.id)
        out.append(
            {
                "id": defn.id,
                "kind": defn.kind,
                "aliases": list(defn.aliases),
                "protected": defn.is_protected,
                "skills": list(spec.skills or ()) if spec else [],
            }
        )
    return out


__all__ = [
    "ROLE_KINDS",
    "catalog_mtime",
    "delete_role",
    "ensure_seeded",
    "get_role_def",
    "list_role_defs",
    "roles_for_lab",
    "upsert_role",
]
