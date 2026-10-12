"""Install a folder or zip the operator dropped. No SKILL.md rewrite."""

from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path

from orchestrator.skills.install.openskills import pack_dirs
from orchestrator.skills.install.place import place_pack
from orchestrator.utils.paths import normalize_rel_path, safe_join


def install_directory(source: Path, *, origin: str = "") -> list[str]:
    root = Path(source)
    packs = pack_dirs(root)
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
    with tempfile.TemporaryDirectory(prefix="peon-skill-drop-") as tmp:
        root = Path(tmp)
        for rel, payload in files:
            target = safe_join(root, rel)
            if target is None:
                raise ValueError(f"unsafe path {rel!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        return install_directory(root, origin=origin)
