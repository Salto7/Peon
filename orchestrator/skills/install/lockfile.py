"""Lockfile for packs installed into the imported skills root."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from orchestrator.config import get_config


def lock_path() -> Path:
    """Lives inside the imported root so one directory mount covers it."""
    return Path(get_config().skills_imported_dir) / "skills_imported.lock.json"


def content_hash(skill_md: Path) -> str:
    digest = hashlib.sha256(skill_md.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def record(
    *,
    name: str,
    source: str,
    revision: str = "",
    skill_md: Path,
) -> None:
    path = lock_path()
    data: dict = {"skills": {}}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict) and isinstance(loaded.get("skills"), dict):
            data = loaded
    data.setdefault("skills", {})
    data["skills"][name] = {
        "source": source,
        "revision": revision,
        "name": name,
        "content_hash": content_hash(skill_md),
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
