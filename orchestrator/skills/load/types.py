"""Load-layer DTOs (discover / activate)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SkillCatalogEntry:
    name: str
    description: str
    location: str
    tags: tuple[str, ...] = ()
    category: str = ""
    jobable: bool = True


@dataclass
class SkillActivation:
    name: str
    description: str
    instructions: str
    skill_dir: str
    resources: list[str] = field(default_factory=list)
