"""Disk manifest → Skill (no agent load, no script execution)."""

from __future__ import annotations

from pathlib import Path

from orchestrator.skills.mcp import normalize_mcp_servers
from orchestrator.skills.model import Skill
from orchestrator.skills.parser import SkillParser
from orchestrator.skills.common import list_resource_files
from orchestrator.utils.service import SharedServiceBase


class SkillProvisioner(SharedServiceBase):
    def __init__(self, *, parser: SkillParser | None = None) -> None:
        self._parser = parser or SkillParser.shared()

    def provision_manifest(
        self, manifest: Path, *, root: Path, source: str = "local"
    ) -> Skill:
        parsed = self._parser.parse_skill_md(manifest)
        return Skill.from_parsed(
            parsed,
            root=root,
            source=source,
            manifest=manifest,
            assets=list_resource_files(manifest.parent),
            mcp_servers=normalize_mcp_servers(parsed.get("mcp")),
        )
