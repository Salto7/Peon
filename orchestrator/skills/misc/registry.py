"""Discover provisioned skills from disk (registry index only)."""

from __future__ import annotations

import os
from pathlib import Path

from orchestrator.config import get_config
from orchestrator.skills.misc.catalog import filter_skills, format_catalog
from orchestrator.skills.misc.skill import Skill
from orchestrator.skills.misc.tags import TagNormalizer
from orchestrator.skills.misc.utils import (
    SKILL_MANIFEST,
    SKILL_SUFFIX,
    find_manifest,
    list_resource_files,
    resolve_alias,
)
from orchestrator.skills.provision import SkillProvisioner
from orchestrator.utils.service import SharedService


class SkillRegistry(SharedService):
    """Scan skill directories via SkillProvisioner; cache and resolve by name."""

    def __init__(
        self,
        *,
        provisioner: SkillProvisioner | None = None,
        skills_dir: Path | None = None,
        skills_external_dirs: list[Path] | None = None,
    ) -> None:
        self._provisioner = provisioner or SkillProvisioner.shared()
        self._skills_dir = skills_dir
        self._skills_external_dirs = skills_external_dirs
        self._cache: dict[str, Skill] = {}
        self._aliases: dict[str, str] = {}
        self._loaded = False
        self._cache_mtime: float = 0.0

    def _roots(self) -> list[tuple[Path, str, int]]:
        cfg = get_config()
        roots: list[tuple[Path, str, int]] = []
        external = self._skills_external_dirs
        if external is None:
            external = list(cfg.skills_external_dirs)
        for idx, path in enumerate(external):
            resolved = Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()
            if resolved.is_dir():
                roots.append((resolved, "external", 10 + idx))
        primary = Path(self._skills_dir or cfg.skills_dir).resolve()
        if primary.is_dir():
            roots.append((primary, "local", 0))
        return roots

    def _find_manifests(self, root: Path) -> list[Path]:
        manifests: list[Path] = []
        seen_names: set[str] = set()

        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            # Shared library package mounted into sandboxes — not a skill.
            if child.name in {"helpers", "__pycache__"}:
                continue
            skill_md = find_manifest(child)
            if skill_md is not None:
                manifests.append(skill_md)
                seen_names.add(child.name)

        for manifest in sorted(root.glob(f"*{SKILL_SUFFIX}")):
            if manifest.name.startswith(".") or manifest.name in {
                SKILL_MANIFEST,
                "skill.md",
            }:
                continue
            if manifest.stem in seen_names:
                continue
            manifests.append(manifest)

        return manifests

    def _scan(self) -> dict[str, Skill]:
        found: dict[str, tuple[Skill, int]] = {}
        for root, source, priority in self._roots():
            for manifest in self._find_manifests(root):
                try:
                    skill = self._provisioner.provision_manifest(
                        manifest, root=root, source=source
                    )
                except OSError:
                    continue
                prev = found.get(skill.name)
                if prev is None or priority < prev[1]:
                    found[skill.name] = (skill, priority)

        skills = {name: s for name, (s, _) in found.items()}
        known_tags = TagNormalizer.collect_known(s.tags for s in skills.values())
        for skill in skills.values():
            skill.tags = TagNormalizer.normalize(skill.tags, known=known_tags)

        aliases: dict[str, str] = {}
        for skill in skills.values():
            for alias in skill.aliases:
                key = str(alias or "").strip()
                if key and key not in skills:
                    aliases[key] = skill.name
        self._aliases = aliases
        return skills

    def _disk_mtime(self) -> float:
        latest = 0.0
        for root, _, _ in self._roots():
            if not root.is_dir():
                continue
            for manifest in self._find_manifests(root):
                try:
                    latest = max(latest, manifest.stat().st_mtime)
                    for asset in list_resource_files(manifest.parent):
                        p = manifest.parent / asset
                        if p.is_file():
                            latest = max(latest, p.stat().st_mtime)
                except OSError:
                    continue
        return latest

    def get_registry(self) -> dict[str, Skill]:
        disk_mtime = self._disk_mtime()
        if not self._loaded or disk_mtime > self._cache_mtime:
            self._cache.clear()
            self._cache.update(self._scan())
            self._cache_mtime = disk_mtime
            self._loaded = True
        return self._cache

    def reload_skills(self) -> dict:
        """Force-rescan skill dirs (same path as mtime-driven ``get_registry``)."""
        if self._loaded:
            before = {n: (s.description, s.mtime) for n, s in self._cache.items()}
        else:
            before = {
                n: (s.description, s.mtime) for n, s in self.get_registry().items()
            }
        self._cache.clear()
        self._loaded = False
        self._cache_mtime = 0.0
        after = self.get_registry()

        diff: dict[str, list[dict]] = {"added": [], "removed": [], "modified": []}
        for n in sorted(set(after) - set(before)):
            diff["added"].append({"name": n, "description": after[n].description})
        for n in sorted(set(before) - set(after)):
            diff["removed"].append({"name": n, "description": before[n][0]})
        for n in sorted(set(before) & set(after)):
            if before[n] != (after[n].description, after[n].mtime):
                diff["modified"].append(
                    {"name": n, "description": after[n].description}
                )
        return diff

    def skill_aliases(self) -> dict[str, str]:
        self.get_registry()
        return dict(self._aliases)

    def resolve_skill_name(self, name: str) -> str:
        return resolve_alias(name, aliases=self.skill_aliases())

    def load_skill(self, name: str) -> Skill | None:
        return self.get_registry().get(self.resolve_skill_name(name))

    def discover_skills(self) -> list[dict]:
        return [
            s.to_catalog_dict()
            for s in sorted(self.get_registry().values(), key=lambda x: x.name)
        ]

    def skills_index(
        self,
        *,
        jobable_only: bool = False,
    ) -> str:
        return format_catalog(
            filter_skills(
                self.get_registry().values(),
                jobable_only=jobable_only,
            ),
            mode="index",
            header="Available skills (use skill_view to load full content).",
        )
