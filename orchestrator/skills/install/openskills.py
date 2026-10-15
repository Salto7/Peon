"""Install a marketplace pack with ``npx openskills install --universal``."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from orchestrator.skills.install.place import place_pack
from orchestrator.skills.manifest import find_skill_manifest
from orchestrator.utils.install_skip import skip_dir_name


def _reject_source(source: str) -> str:
    text = str(source or "").strip()
    if not text or "\n" in text or "\r" in text or "\x00" in text:
        raise ValueError("skill source is empty or contains a newline")
    return text


def pack_dirs(root: Path) -> list[Path]:
    """Skill folders at or under ``root``, at most four levels down."""
    found: list[Path] = []

    def walk(path: Path, depth: int) -> None:
        if find_skill_manifest(path) is not None:
            found.append(path)
            return
        if depth >= 4 or not path.is_dir():
            return
        try:
            children = sorted(path.iterdir())
        except OSError:
            return
        for child in children:
            # Skills may live under ``.claude/skills`` — only skip junk dirs here.
            if not child.is_dir() or skip_dir_name(child.name, skip_hidden=False):
                continue
            walk(child, depth + 1)

    walk(Path(root), 0)
    return found


def install_openskills(source: str, *, revision: str = "") -> list[str]:
    """Install into the imported root. Bytes on disk are the pack; SKILL.md is unchanged."""
    origin = _reject_source(source)
    with tempfile.TemporaryDirectory(prefix="peon-openskills-") as tmp:
        try:
            env = os.environ.copy()
            env["CI"] = "1"
            proc = subprocess.run(
                ["npx", "-y", "openskills", "install", origin, "--universal", "-y"],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "npx is required to install marketplace skills"
            ) from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(detail or "openskills install failed")
        packs = pack_dirs(Path(tmp) / ".agent" / "skills")
        if not packs:
            raise RuntimeError("openskills install produced no SKILL.md pack")
        names = [
            place_pack(pack, source=origin, revision=revision) for pack in packs
        ]
    return names
