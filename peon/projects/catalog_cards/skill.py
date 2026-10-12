"""UI cards for skill packs (skills/*/SKILL.md)."""

from __future__ import annotations

from typing import Any

from orchestrator.skills.registry import SkillRegistry
from peon.projects.catalog_cards.base import CatalogCardsBase


class SkillCards(CatalogCardsBase):
    """UI cards for SkillRegistry packs."""

    SECTION = "skills"
    LABEL = "Skills"
    RELOAD_URL_NAME = "catalog_api_skills_reload"
    RELOAD_TITLE = "Rescan skills/ via SkillRegistry.reload_skills"

    @classmethod
    def reload(cls) -> dict[str, list]:
        SkillRegistry.shared().reload_skills()
        return {"skills": [s.id for s in SkillRegistry.shared().list_skills()]}

    @classmethod
    def card(
        cls,
        *,
        id: str,
        description: str = "",
        tools: list | None = None,
        tags: list | None = None,
        desc_limit: int | None = None,
        tag_limit: int | None = None,
    ) -> dict:
        skill_id = cls.text(id)
        return {
            "id": skill_id,
            **cls.base(
                name=skill_id,
                description=description,
                tags=tags,
                desc_limit=desc_limit,
                tag_limit=tag_limit,
            ),
            "tools": cls.labels(tools),
        }

    @classmethod
    def from_skill(cls, skill: Any, **kwargs: Any) -> dict:
        return cls.card(
            id=skill.id,
            description=skill.description or "",
            tools=list(skill.tools or ()),
            tags=list(skill.tags or ()),
            **kwargs,
        )

    @classmethod
    def catalog(cls, **kwargs: Any) -> list[dict]:
        del kwargs
        return [cls.from_skill(s) for s in SkillRegistry.shared().list_skills()]
