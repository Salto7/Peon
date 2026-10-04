"""Role catalog: load ROLE.yaml packs, query hierarchy, cache by id."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from orchestrator.config import get_config
from orchestrator.crew.roles.model import RoleSpec
from orchestrator.packs import list_role_resource_files
from orchestrator.utils.commands import clear_command_caches
from orchestrator.utils.service import SharedServiceBase
from orchestrator.utils.strings import as_str_list


def _str_list(raw: Any) -> tuple[str, ...]:
    return tuple(as_str_list(raw))


def load_role_file(path: Path) -> RoleSpec:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"ROLE.yaml must be a mapping: {path}")
    root = path.parent
    rid = str(data.get("id") or root.name).strip()
    if not rid:
        raise ValueError(f"role id missing: {path}")
    policy = data.get("tool_policy") if isinstance(data.get("tool_policy"), dict) else {}
    hierarchy = data.get("hierarchy") if isinstance(data.get("hierarchy"), dict) else {}
    label = str(data.get("label") or rid).strip()
    crew_role = str(data.get("crew_role") or label).strip()
    caps = _str_list(data.get("capabilities"))
    mode = str(data.get("mode") or "").strip().lower()
    if not mode:
        mode = (
            "authoring"
            if {c.lower() for c in caps} & {"authoring", "draft", "learn"}
            else "engagement"
        )
    assets = _str_list(data.get("assets"))
    if not assets:
        assets = tuple(list_role_resource_files(root))
    authoring = data.get("authoring") if isinstance(data.get("authoring"), dict) else {}
    tasks_raw = authoring.get("tasks") if isinstance(authoring.get("tasks"), dict) else {}
    authoring_tasks = tuple(
        (str(k).strip().lower(), str(v).strip())
        for k, v in tasks_raw.items()
        if str(k).strip() and str(v).strip()
    )
    return RoleSpec(
        id=rid,
        label=label,
        crew_role=crew_role,
        goal=str(data.get("goal") or "").strip(),
        backstory=str(data.get("backstory") or "").strip(),
        tools=_str_list(data.get("tools")),
        allow_binaries=_str_list(policy.get("allow_binaries")),
        reports_to=str(hierarchy.get("reports_to") or data.get("reports_to") or "").strip(),
        capabilities=caps,
        knowledge_files=_str_list(data.get("knowledge") or data.get("knowledge_files")),
        assets=assets,
        mode=mode,
        requires_roe=bool(data.get("requires_roe", False)),
        allow_delegation=bool(data.get("allow_delegation", False)),
        reasoning=bool(data.get("reasoning", False)),
        advanced_reasoning=bool(data.get("advanced_reasoning", False)),
        max_iter=max(1, int(data.get("max_iter") or 20)),
        authoring_engine=str(authoring.get("engine") or "").strip().lower(),
        authoring_bootstrap=str(authoring.get("bootstrap") or "").strip(),
        authoring_tasks=authoring_tasks,
        root=root,
    )


def discover_role_files(roles_dir: Path) -> list[Path]:
    root = Path(roles_dir)
    if not root.is_dir():
        return []
    found: list[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        for name in ("ROLE.yaml", "ROLE.yml"):
            path = child / name
            if path.is_file():
                found.append(path)
                break
    return found


class RoleRegistry(SharedServiceBase):
    """Scan ``roles/`` and cache RoleSpec by id."""

    def __init__(self) -> None:
        self._by_id: dict[str, RoleSpec] | None = None
        self._mtime: float = -1.0

    def roles_dir(self) -> Path:
        return Path(get_config().roles_dir)

    def invalidate(self) -> None:
        self._by_id = None
        self._mtime = -1.0
        try:
            clear_command_caches()
        except Exception:
            pass

    def _disk_mtime(self) -> float:
        root = self.roles_dir()
        if not root.is_dir():
            return 0.0
        latest = root.stat().st_mtime
        for path in discover_role_files(root):
            latest = max(latest, path.stat().st_mtime)
        return latest

    def _scan(self) -> dict[str, RoleSpec]:
        out: dict[str, RoleSpec] = {}
        for path in discover_role_files(self.roles_dir()):
            role = load_role_file(path)
            out[role.id] = role
        return out

    def get_registry(self) -> dict[str, RoleSpec]:
        mtime = self._disk_mtime()
        if self._by_id is None or mtime != self._mtime:
            self._by_id = self._scan()
            self._mtime = mtime
        return self._by_id

    def get(self, role_id: str) -> RoleSpec | None:
        rid = (role_id or "").strip()
        if not rid:
            return None
        return self.get_registry().get(rid)

    def list_roles(self) -> list[RoleSpec]:
        return sorted(self.get_registry().values(), key=lambda r: r.id)

    def require(self, role_id: str) -> RoleSpec:
        role = self.get(role_id)
        if role is None:
            known = ", ".join(r.id for r in self.list_roles()) or "(none)"
            raise KeyError(f"unknown role {role_id!r}; known: {known}")
        return role


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


def _is_manager_specialist(
    role: RoleSpec, *, skip: set[str], mgr_id: str
) -> bool:
    if role.id in skip or role.is_authoring or role.is_manager or role.is_analyzer:
        return False
    if not mgr_id:
        return True
    return role.reports_to == mgr_id


def specialists_for(
    role_ids: list[str] | tuple[str, ...] | None = None,
    *,
    reg: RoleRegistry | None = None,
) -> list[RoleSpec]:
    """Specialists that report to the manager."""
    registry = reg or RoleRegistry.shared()
    mgr = manager_role(registry)
    ana = analyzer_role(registry)
    skip = {r.id for r in (mgr, ana) if r is not None}
    mgr_id = mgr.id if mgr else ""

    requested = [str(r).strip() for r in (role_ids or ()) if str(r).strip()]
    if not requested:
        return [
            r
            for r in registry.list_roles()
            if _is_manager_specialist(r, skip=skip, mgr_id=mgr_id)
        ]

    out: list[RoleSpec] = []
    seen: set[str] = set()
    for rid in requested:
        if rid in skip or rid in seen:
            continue
        role = registry.get(rid)
        if role is None or not _is_manager_specialist(
            role, skip=skip, mgr_id=mgr_id
        ):
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


def engagement_start_role() -> str:
    start, _ = engagement_bookends()
    return start


def engagement_end_role() -> str:
    _, end = engagement_bookends()
    return end
