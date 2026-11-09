"""Source-aware recursive SKILL.md discovery."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from orchestrator.skills.common import find_manifest

logger = logging.getLogger(__name__)

_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "htmlcov",
    }
)



@dataclass(frozen=True)
class SkillCandidate:
    """One discovered skill pack root + manifest path."""

    root: Path
    manifest: Path
    source: str  # native | imported | project | personal | plugin | additional
    namespace: str = ""


def _is_skipped_dir(path: Path) -> bool:
    name = path.name
    if name in _SKIP_DIR_NAMES:
        return True
    # Allow known agent skill roots; skip other dot-dirs.
    return name.startswith(".") and name not in {".claude", ".agents", ".agent", ".cursor"}


def discover_skill_candidates(
    skills_dir: Path,
    *,
    source: str = "native",
    max_depth: int = 8,
    skip_helpers: bool = True,
) -> list[SkillCandidate]:
    """Recursively find skill packs under ``skills_dir``.

    A pack is any directory containing ``SKILL.md`` / ``skill.md``. Symlinked
    directories are deduplicated by resolved path. Diagnostics are logged; this
    function never raises for individual path errors.
    """
    root = Path(skills_dir)
    if not root.is_dir():
        return []

    found: list[SkillCandidate] = []
    seen_roots: set[Path] = set()

    def walk(path: Path, depth: int, namespace: str) -> None:
        if depth > max_depth:
            return
        try:
            if not path.is_dir():
                return
        except OSError as exc:
            logger.debug("skill scan skip %s: %s", path, exc)
            return

        # Treat current dir as a pack if it has a manifest.
        manifest = find_manifest(path)

        if manifest is not None:
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            if resolved in seen_roots:
                return
            seen_roots.add(resolved)
            # Namespace set while descending into ``<ns>/skills/`` (see walk).
            ns = namespace
            found.append(
                SkillCandidate(
                    root=path,
                    manifest=manifest,
                    source=source,
                    namespace=ns,
                )
            )
            # Do not descend into an already-identified skill pack.
            return

        try:
            children = sorted(path.iterdir())
        except OSError as exc:
            logger.debug("skill scan list failed %s: %s", path, exc)
            return

        for child in children:
            try:
                if not child.is_dir():
                    continue
            except OSError:
                continue
            if _is_skipped_dir(child):
                continue
            if skip_helpers and child.name == "helpers" and depth == 0:
                continue
            # When entering ``…/<plugin>/skills/``, remember the plugin id.
            next_ns = namespace
            if child.name in {"skills", "skill"} and not namespace:
                next_ns = path.name
            walk(child, depth + 1, next_ns)

    walk(root, 0, "")
    found.sort(key=lambda c: (c.source, c.namespace, c.root.as_posix().lower()))
    return found


__all__ = [
    "SkillCandidate",
    "discover_skill_candidates",
]
