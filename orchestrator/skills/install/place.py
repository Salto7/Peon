"""Copy a validated pack into the imported root. Does not rewrite SKILL.md."""

from __future__ import annotations

import shutil
from pathlib import Path

from orchestrator.config import get_config
from orchestrator.skills.install.lockfile import record
from orchestrator.skills.install.validate import validate_pack
from orchestrator.skills.manifest import ParseProfile, SkillParseError, find_skill_manifest
from orchestrator.skills.model import parse_skill_md
from orchestrator.skills.providers.base import PEON_PROVIDER, SkillId
from orchestrator.skills.registry import SkillRegistry


def _frontmatter_name(skill_dir: Path) -> str:
    manifest = find_skill_manifest(skill_dir)
    if manifest is None:
        raise ValueError(f"{skill_dir} has no SKILL.md")
    try:
        spec = parse_skill_md(manifest, profile=ParseProfile.AGENTSKILLS)
    except SkillParseError as exc:
        raise ValueError(exc.message) from exc
    parsed = SkillId.parse(spec.id)
    if parsed is None or parsed.provider != PEON_PROVIDER:
        raise ValueError(f"{skill_dir.name} frontmatter name is not a bare skill id")
    return parsed.name


def place_pack(skill_dir: Path, *, source: str, revision: str = "") -> str:
    """Validate, reject Peon-name collisions, then copy next to native packs."""
    root = Path(skill_dir)
    manifest = find_skill_manifest(root)
    if manifest is None:
        raise ValueError(f"{root} has no SKILL.md")
    try:
        manifest_matches = [
            path
            for path in root.iterdir()
            if path.is_file() and path.name.lower() == "skill.md"
        ]
    except OSError as exc:
        raise ValueError(f"could not inspect {root}: {exc}") from exc
    if len(manifest_matches) > 1:
        names = ", ".join(sorted(path.name for path in manifest_matches))
        raise ValueError(f"{root} contains duplicate skill manifests: {names}")
    errors = validate_pack(root)
    if errors:
        raise ValueError("; ".join(errors))
    name = _frontmatter_name(root)
    peon = SkillRegistry.shared().get(name)
    if peon is not None and peon.provider == PEON_PROVIDER:
        raise ValueError(f"skill name {name} is already a Peon skill")
    dest_root = Path(get_config().skills_imported_dir)
    dest_root.mkdir(parents=True, exist_ok=True)
    dest = dest_root / name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(root, dest)
    copied_manifest = dest / manifest.name
    canonical_manifest = dest / "SKILL.md"
    if copied_manifest != canonical_manifest:
        if canonical_manifest.exists():
            raise ValueError(
                f"{root} contains both {manifest.name} and SKILL.md"
            )
        copied_manifest.rename(canonical_manifest)
    record(name=name, source=source, revision=revision, skill_md=canonical_manifest)
    SkillRegistry.shared().invalidate()
    return name
