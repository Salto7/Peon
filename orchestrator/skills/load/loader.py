"""Progressive disclosure loaders (discover / activate / read)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from orchestrator.skills.load.types import SkillActivation, SkillCatalogEntry
from orchestrator.skills.misc.catalog import filter_skills, format_catalog
from orchestrator.skills.misc.registry import SkillRegistry
from orchestrator.skills.misc.utils import list_resource_files, resolve_resource
from orchestrator.utils.service import SharedService


class SkillLoader(ABC):
    @abstractmethod
    def discover(self, *, jobable_only: bool = False) -> list[SkillCatalogEntry]: ...

    @abstractmethod
    def activate(self, name: str) -> SkillActivation: ...

    @abstractmethod
    def read_resource(self, name: str, relative_path: str) -> str: ...

    def discover_prompt(self, *, jobable_only: bool = True) -> str:
        entries = self.discover(jobable_only=jobable_only)
        if not entries:
            return "No skills installed."
        lines = ["Available skills (activate to load full instructions):"]
        lines.extend(f"- {e.name}: {e.description}" for e in entries)
        return "\n".join(lines)


class FilesystemSkillLoader(SharedService, SkillLoader):
    def __init__(self, *, registry: SkillRegistry | None = None) -> None:
        self._registry = registry or SkillRegistry.shared()

    def discover(self, *, jobable_only: bool = False) -> list[SkillCatalogEntry]:
        out: list[SkillCatalogEntry] = []
        for s in filter_skills(
            self._registry.get_registry().values(),
            jobable_only=jobable_only,
        ):
            entry = s.to_catalog_entry()
            out.append(
                SkillCatalogEntry(
                    name=entry["name"],
                    description=entry["description"],
                    location=entry["location"],
                    tags=tuple(entry["tags"]),
                    category=entry["category"],
                    jobable=entry["jobable"],
                )
            )
        return sorted(out, key=lambda e: e.name)

    def discover_prompt(self, *, jobable_only: bool = True) -> str:
        return format_catalog(
            filter_skills(
                self._registry.get_registry().values(),
                jobable_only=jobable_only,
            ),
            mode="discover",
            header="Available skills (activate to load full instructions):",
        )

    def activate(self, name: str) -> SkillActivation:
        skill = self._registry.load_skill(name)
        if not skill:
            raise ValueError(f"Skill not found: {name}")
        return SkillActivation(
            name=skill.name,
            description=skill.description,
            instructions=skill.instructions,
            skill_dir=str(skill.skill_dir),
            resources=list(skill.assets) or list_resource_files(skill.skill_dir),
        )

    def read_resource(self, name: str, relative_path: str) -> str:
        skill = self._registry.load_skill(name)
        if not skill:
            raise ValueError(f"Skill not found: {name}")
        path = relative_path.strip()
        if not path:
            return skill.manifest_path.read_text(encoding="utf-8")
        target = resolve_resource(skill.skill_dir, path)
        if not target:
            raise ValueError(f"File not found: {path}")
        return target.read_text(encoding="utf-8")
