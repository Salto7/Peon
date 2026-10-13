"""UI cards for CrewAI roles (RoleRegistry)."""

from __future__ import annotations

from typing import Any

from orchestrator.crew.roles.registry import RoleRegistry
from peon.projects.catalog_cards.base import CatalogCardsBase


class RoleCards(CatalogCardsBase):
    """UI cards for platform engagement roles + affinity skills."""

    SECTION = "roles"
    LABEL = "Roles"
    RELOAD_URL_NAME = "catalog_api_roles_reload"
    RELOAD_TITLE = "Rescan platform roles + skill affinity via RoleRegistry"

    @classmethod
    def reload(cls) -> dict[str, list]:
        RoleRegistry.shared().invalidate()
        return {"roles": [r.id for r in RoleRegistry.shared().list_roles()]}

    @classmethod
    def card(
        cls,
        *,
        name: str,
        description: str = "",
        category: str = "",
        tags: list | None = None,
        required: bool = False,
        desc_limit: int | None = None,
        tag_limit: int | None = None,
        **_kwargs: Any,
    ) -> dict:
        return {
            **cls.base(
                name=name,
                description=description,
                tags=tags,
                desc_limit=desc_limit,
                tag_limit=tag_limit,
            ),
            "category": cls.text(category),
            "required": bool(required),
            "jobable": True,
            "compatible": True,
            "lint_issues": [],
            "aliases": [],
            "lifecycle": "",
        }

    @staticmethod
    def required_names() -> list[str]:
        """Bookend roles for multi-phase projects (discovered from hierarchy)."""
        out: list[str] = []
        registry = RoleRegistry.shared()
        mgr = registry.manager()
        ana = registry.analyzer()
        if mgr:
            out.append(mgr.id)
        if ana and ana.id not in out:
            out.append(ana.id)
        return out

    @classmethod
    def from_role(cls, role, *, desc_limit: int | None = None, tag_limit: int | None = None) -> dict:
        from orchestrator.crew.roles.catalog import catalog_role

        required = set(cls.required_names())
        defn = catalog_role(role.id)
        card = cls.card(
            name=role.id,
            description=role.goal or role.backstory or "",
            category=role.crew_role or "",
            tags=list(role.capabilities or []),
            required=role.id in required,
            desc_limit=desc_limit,
            tag_limit=tag_limit,
        )
        card["aliases"] = list(defn.aliases) if defn else []
        card["skills"] = list(role.skills or ())
        card["kind"] = defn.kind if defn else getattr(role, "kind", "") or ""
        card["deletable"] = not bool(defn.is_protected) if defn else False
        return card

    @classmethod
    def from_registry(cls, name: str) -> dict:
        role = RoleRegistry.shared().get(name)
        if role is None:
            return cls.card(name=name)
        return cls.from_role(role)

    @classmethod
    def for_names(cls, names: list[str]) -> list[dict]:
        return [cls.from_registry(n) for n in names if str(n).strip()]

    @classmethod
    def catalog(cls, **kwargs: Any) -> list[dict]:
        del kwargs
        return [cls.from_role(r) for r in RoleRegistry.shared().list_roles()]

    @classmethod
    def picker(cls) -> list[dict]:
        required = set(cls.required_names())
        roles = sorted(
            RoleRegistry.shared().list_roles(),
            key=lambda r: (0 if r.id in required else 1, r.id),
        )
        return [
            cls.from_role(
                r,
                desc_limit=cls.PICKER_DESC_LIMIT,
                tag_limit=cls.PICKER_TAG_LIMIT,
            )
            for r in roles
        ]
