"""Fetch a marketplace source (GitHub repo or uploaded zip) to a scratch dir
that persists across the scan → review → import flow.

Reuses the same git-clone and zip-extraction helpers the skill/MCP
installers already use — no new fetch mechanics, just a persistent
destination keyed by the marketplace source id instead of a throwaway
``tempfile.TemporaryDirectory``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from orchestrator.skills.install.sources import _git_clone, _normalize_git
from orchestrator.utils.zip_extract import extract_zip_safe


def _sources_root() -> Path:
    from django.conf import settings

    root = Path(getattr(settings, "PLATFORM_WORKSPACE_DIR", "") or "workspaces/platform") / "marketplace"
    root.mkdir(parents=True, exist_ok=True)
    return root


def source_dir(source_id: str) -> Path:
    """Scratch directory for one marketplace source's fetched content."""
    return _sources_root() / str(source_id)


def fetch_github(source_id: str, url: str, *, ref: str = "") -> Path:
    """Shallow-clone ``url`` (optionally at ``ref``) into this source's scratch dir."""
    origin = _normalize_git(url)
    dest = source_dir(source_id) / "repo"
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _git_clone(origin, dest, revision=ref)
    return dest


def fetch_zip_archive(source_id: str, archive_path: str) -> Path:
    """Extract the stashed upload (see ``archive_path``) into the scratch dir."""
    path = Path(archive_path)
    if not path.is_file():
        raise ValueError(f"archive missing: {path}")
    dest = source_dir(source_id) / "repo"
    if dest.exists():
        shutil.rmtree(dest)
    extract_zip_safe(path.read_bytes(), dest)
    return dest


def scan_root(fetched: Path, subpath: str) -> Path:
    """``fetched``, narrowed to ``subpath`` when one is set. Rejects escapes."""
    sub = str(subpath or "").strip().strip("/")
    if not sub:
        return fetched
    base = fetched.resolve()
    target = (base / sub).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise ValueError("subpath escapes the fetched source") from None
    if not target.is_dir():
        raise ValueError(f"subpath not found in source: {subpath}")
    return target
