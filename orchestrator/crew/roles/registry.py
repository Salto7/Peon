"""In-process role catalog (mirrors SkillRegistry pattern)."""

from __future__ import annotations

from pathlib import Path

from orchestrator.config import get_config
from orchestrator.crew.roles.loader import discover_role_files, load_role_file
from orchestrator.crew.roles.model import RoleSpec
from orchestrator.utils.service import SharedServiceBase


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
