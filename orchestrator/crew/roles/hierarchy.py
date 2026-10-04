"""Derive crew hierarchy from ROLE.yaml (reports_to / flags) — no id hardcoding."""

from __future__ import annotations

from orchestrator.crew.roles.model import RoleSpec
from orchestrator.crew.roles.registry import RoleRegistry


def manager_role(reg: RoleRegistry | None = None) -> RoleSpec | None:
    """Engagement lead: allow_delegation and no supervisor."""
    registry = reg or RoleRegistry.shared()
    for role in registry.list_roles():
        if role.is_manager and not role.is_authoring:
            return role
    for role in registry.list_roles():
        if role.is_manager:
            return role
    return None


def analyzer_role(reg: RoleRegistry | None = None) -> RoleSpec | None:
    """Reporting bookend: capability report (usually reports_to manager)."""
    registry = reg or RoleRegistry.shared()
    mgr = manager_role(registry)
    mgr_id = mgr.id if mgr else ""
    for role in registry.list_roles():
        if role.is_analyzer and (not role.reports_to or role.reports_to == mgr_id):
            return role
    for role in registry.list_roles():
        if role.is_analyzer:
            return role
    return None


def is_manager_role_id(role_id: str, reg: RoleRegistry | None = None) -> bool:
    """Whether a catalog role is manager-class, based only on role metadata."""
    role = (reg or RoleRegistry.shared()).get(str(role_id or "").strip())
    return bool(role and role.is_manager)


def is_analyzer_role_id(role_id: str, reg: RoleRegistry | None = None) -> bool:
    """Whether a catalog role is analyzer-class, based only on role metadata."""
    role = (reg or RoleRegistry.shared()).get(str(role_id or "").strip())
    return bool(role and role.is_analyzer)


def specialists_for(
    role_ids: list[str] | tuple[str, ...] | None = None,
    *,
    reg: RoleRegistry | None = None,
) -> list[RoleSpec]:
    """Specialists that report to the manager.

    If ``role_ids`` is set, keep only those (minus manager/analyzer/authoring).
    If empty, return every engagement specialist under the manager.
    """
    registry = reg or RoleRegistry.shared()
    mgr = manager_role(registry)
    ana = analyzer_role(registry)
    skip = {r.id for r in (mgr, ana) if r is not None}
    mgr_id = mgr.id if mgr else ""

    def _is_specialist(role: RoleSpec) -> bool:
        if role.id in skip or role.is_authoring or role.is_manager or role.is_analyzer:
            return False
        if not mgr_id:
            return True
        return role.reports_to == mgr_id

    requested = [str(r).strip() for r in (role_ids or ()) if str(r).strip()]
    if not requested:
        return [r for r in registry.list_roles() if _is_specialist(r)]

    out: list[RoleSpec] = []
    seen: set[str] = set()
    for rid in requested:
        if rid in skip or rid in seen:
            continue
        role = registry.get(rid)
        if role is None or not _is_specialist(role):
            continue
        seen.add(rid)
        out.append(role)
    return out


def engagement_bookends(reg: RoleRegistry | None = None) -> tuple[str, str]:
    """``(manager_id, analyzer_id)`` for planners — empty string if missing."""
    registry = reg or RoleRegistry.shared()
    mgr = manager_role(registry)
    ana = analyzer_role(registry)
    return (mgr.id if mgr else "", ana.id if ana else "")
