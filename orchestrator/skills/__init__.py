"""Skills package: model, registry, catalog, execute, provision.

Prefer leaf imports; package root re-exports the common types::

    from orchestrator.skills import Skill, SkillRegistry, SkillRouter
"""

from orchestrator.skills.matcher import SkillNameMatcher
from orchestrator.skills.model import Skill
from orchestrator.skills.parser import SkillParser
from orchestrator.skills.registry import SkillRegistry
from orchestrator.skills.router import SkillRouter
from orchestrator.skills.tags import TagNormalizer

__all__ = [
    "Skill",
    "SkillNameMatcher",
    "SkillParser",
    "SkillRegistry",
    "SkillRouter",
    "TagNormalizer",
]
