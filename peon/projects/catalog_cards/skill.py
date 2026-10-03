"""UI cards for filesystem skills (SkillRegistry)."""

from __future__ import annotations

from typing import Any

from orchestrator.skills.registry import SkillRegistry
from orchestrator.skills.common import CORE_SKILLS
from orchestrator.skills.provision import SkillLinter
from peon.projects.catalog_cards_base import CatalogCardsBase


class SkillCards(CatalogCardsBase):
    """UI cards for filesystem skills (SkillRegistry)."""

    SECTION = "skills"
    LABEL = "Skills"
    RELOAD_URL_NAME = "catalog_api_skills_reload"
    RELOAD_TITLE = "Rescan SKILLS_DIR via SkillRegistry.reload_skills"

    @classmethod
    def reload(cls) -> dict[str, list]:
        return SkillRegistry.shared().reload_skills()

    @classmethod
    def card(
        cls,
        *,
        name: str,
        description: str = "",
        category: str = "",
        tags: list | None = None,
        lifecycle: str = "",
        aliases: list | None = None,
        jobable: bool = True,
        required: bool = False,
        compatible: bool = True,
        lint_issues: list | None = None,
        desc_limit: int | None = None,
        tag_limit: int | None = None,
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
            "lifecycle": cls.text(lifecycle),
            "aliases": cls.labels(aliases, limit=cls.ALIAS_LIMIT),
            "jobable": bool(jobable),
            "required": bool(required),
            "compatible": bool(compatible),
            "lint_issues": [cls.text(m) for m in (lint_issues or []) if str(m).strip()],
        }

    @staticmethod
    def required_names() -> list[str]:
        """Core skills always included on every project (cannot be deselected)."""
        return sorted(CORE_SKILLS)

    @classmethod
    def lint_fields(cls, skill_dir) -> tuple[bool, list[str]]:
        """Return (compatible, error messages) for catalog hazard UI."""
        checked = SkillLinter.shared().check(skill_dir)
        messages_out = [
            str(i.get("message") or "").strip()
            for i in (checked.get("errors") or [])
            if str(i.get("message") or "").strip()
        ]
        return bool(checked.get("compatible")), messages_out

    @classmethod
    def from_skill(cls, skill, *, lint: bool = True, **card_kw: Any) -> dict:
        compatible, lint_issues = (True, [])
        if lint:
            compatible, lint_issues = cls.lint_fields(skill.skill_dir)
        return cls.card(
            name=skill.name,
            description=skill.description or "",
            category=skill.category or "",
            tags=list(skill.tags or []),
            lifecycle=str(skill.lifecycle or ""),
            aliases=list(skill.aliases or []),
            jobable=bool(skill.jobable),
            required=bool(skill.is_builtin),
            compatible=compatible,
            lint_issues=lint_issues,
            **card_kw,
        )

    @classmethod
    def from_registry(cls, name: str) -> dict:
        skill = SkillRegistry.shared().load_skill(name)
        if skill is None:
            return cls.card(name=name)
        return cls.from_skill(skill)

    @classmethod
    def for_names(cls, names: list[str]) -> list[dict]:
        return [cls.from_registry(n) for n in names if str(n).strip()]

    @classmethod
    def catalog(cls, *, jobable_only: bool = True, **kwargs: Any) -> list[dict]:
        del kwargs
        skills = sorted(
            SkillRegistry.shared().get_registry().values(),
            key=lambda s: s.name,
        )
        return [
            cls.from_skill(s)
            for s in skills
            if not jobable_only or s.jobable
        ]

    @classmethod
    def picker(cls) -> list[dict]:
        """Jobable skills for the create-project picker (compact cards).

        Core skills (`CORE_SKILLS`) are marked ``required`` and listed first.
        """
        skills = sorted(
            SkillRegistry.shared().get_registry().values(),
            key=lambda s: (0 if s.is_builtin else 1, s.name),
        )
        return [
            cls.from_skill(
                s,
                lint=False,
                desc_limit=cls.PICKER_DESC_LIMIT,
                tag_limit=cls.PICKER_TAG_LIMIT,
            )
            for s in skills
            if s.jobable
        ]

