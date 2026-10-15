"""Install a folder or zip the operator dropped. No SKILL.md rewrite."""

from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path

from orchestrator.skills.install.openskills import pack_dirs
from orchestrator.skills.install.place import _frontmatter_name, place_pack
from orchestrator.utils.install_skip import skip_rel_path
from orchestrator.utils.paths import normalize_rel_path, safe_join


def _pack_rank(path: Path, name: str) -> tuple[int, int, str]:
    """Prefer ``…/skills/<name>`` over e.g. ``docs/`` with the same frontmatter name."""
    parts = [p.lower() for p in path.parts]
    dir_match = 0 if path.name.lower() == name else 1
    under_skills = 0 if any(p in {"skills", "skill"} for p in parts) else 1
    return (dir_match, under_skills, path.as_posix().lower())


def _unique_packs(packs: list[Path]) -> list[Path]:
    """One pack per skill id — keep the best-ranked directory."""
    best: dict[str, Path] = {}
    for pack in packs:
        try:
            name = _frontmatter_name(pack)
        except ValueError:
            continue
        prev = best.get(name)
        if prev is None or _pack_rank(pack, name) < _pack_rank(prev, name):
            best[name] = pack
    return [best[key] for key in sorted(best)]


def install_directory(source: Path, *, origin: str = "") -> list[str]:
    root = Path(source)
    packs = _unique_packs(pack_dirs(root))
    if not packs:
        raise ValueError("dropped folder has no SKILL.md")
    label = origin or f"drop:{root.name}"
    return [place_pack(pack, source=label) for pack in packs]


def install_zip(data: bytes, *, filename: str = "pack.zip") -> list[str]:
    if not data:
        raise ValueError("empty zip")
    with tempfile.TemporaryDirectory(prefix="peon-skill-zip-") as tmp:
        dest_root = Path(tmp)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    rel = normalize_rel_path(info.filename.rstrip("/"))
                    if rel is None and info.filename.strip("/"):
                        raise ValueError(f"unsafe zip path {info.filename!r}")
                    continue
                rel = normalize_rel_path(info.filename)
                target = safe_join(dest_root, rel)
                if target is None:
                    raise ValueError(f"unsafe zip path {info.filename!r}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
        return install_directory(dest_root, origin=f"zip:{filename}")


def install_relative_files(
    files: list[tuple[str, bytes]],
    *,
    origin: str = "drop",
) -> list[str]:
    """Rebuild a dropped folder from ``(relative path, bytes)`` pairs."""
    if not files:
        raise ValueError("no files")
    # Keep ``.claude/skills/...`` trees; only drop junk dirs (venv, node_modules, …).
    kept = [
        (rel, payload)
        for rel, payload in files
        if not skip_rel_path(rel, skip_hidden=False)
    ]
    if not kept:
        raise ValueError(
            "upload contained only ignored paths (.venv, node_modules, …); "
            "choose the skill pack folder"
        )
    with tempfile.TemporaryDirectory(prefix="peon-skill-drop-") as tmp:
        root = Path(tmp)
        for rel, payload in kept:
            target = safe_join(root, rel)
            if target is None:
                raise ValueError(f"unsafe path {rel!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        return install_directory(root, origin=origin)
