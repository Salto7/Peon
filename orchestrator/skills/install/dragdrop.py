"""Install a folder or zip the operator dropped.

SKILL.md is placed as-is, except its frontmatter ``name:`` is rewritten with
a namespace prefix when the pack was found nested under a ``<plugin>/skills/``
directory (a plugin bundle) — see ``apply_skill_namespace`` for why.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TypedDict

from orchestrator.skills.common import apply_candidate_namespace
from orchestrator.skills.install.place import SkillConflictError, _frontmatter_name, place_pack
from orchestrator.skills.registry_scan import SkillCandidate, discover_skill_candidates
from orchestrator.utils.install_skip import skip_rel_path
from orchestrator.utils.paths import safe_join
from orchestrator.utils.zip_extract import extract_zip_safe


class InstallOutcome(TypedDict):
    """Per-candidate result of installing one or more packs from one source.

    ``conflicts`` names a pack that already exists and ``on_conflict`` was
    ``"ask"`` — nothing was written for it; the caller re-submits the same
    install with ``on_conflict="skip"`` or ``"overwrite"`` once an operator
    decides, rather than the whole batch failing or silently overwriting.
    """

    installed: list[str]
    skipped: list[str]
    conflicts: list[str]
    errors: list[dict[str, str]]


def install_candidates(
    candidates: list[SkillCandidate], *, on_conflict: str
) -> InstallOutcome:
    result: InstallOutcome = {
        "installed": [],
        "skipped": [],
        "conflicts": [],
        "errors": [],
    }
    for candidate in candidates:
        apply_candidate_namespace(candidate)
        try:
            name = place_pack(candidate.root, overwrite=on_conflict == "overwrite")
        except SkillConflictError as exc:
            (result["skipped"] if on_conflict == "skip" else result["conflicts"]).append(
                exc.name
            )
        except ValueError as exc:
            result["errors"].append({"path": str(candidate.root), "error": str(exc)})
        else:
            result["installed"].append(name)
    return result


def _pack_rank(path: Path, name: str) -> tuple[int, int, str]:
    """Prefer ``…/skills/<name>`` over e.g. ``docs/`` with the same frontmatter name."""
    parts = [p.lower() for p in path.parts]
    dir_match = 0 if path.name.lower() == name else 1
    under_skills = 0 if any(p in {"skills", "skill"} for p in parts) else 1
    return (dir_match, under_skills, path.as_posix().lower())


def _unique_candidates(candidates: list[SkillCandidate]) -> list[SkillCandidate]:
    """One pack per (namespace, skill id) — keep the best-ranked directory.

    Two different namespaces (plugin bundles) each keep their own same-named
    pack; within one namespace, duplicates collapse to the best-ranked path.
    """
    best: dict[tuple[str, str], SkillCandidate] = {}
    for candidate in candidates:
        try:
            name = _frontmatter_name(candidate.root)
        except ValueError:
            continue
        key = (candidate.namespace, name)
        prev = best.get(key)
        if prev is None or _pack_rank(candidate.root, name) < _pack_rank(prev.root, name):
            best[key] = candidate
    return [best[key] for key in sorted(best)]


def install_directory(source: Path, *, on_conflict: str = "ask") -> InstallOutcome:
    """Install every pack found under ``source`` (recursive — see
    ``discover_skill_candidates``), so one dropped/disk folder containing
    several skill subfolders installs all of them in one call."""
    root = Path(source)
    candidates = _unique_candidates(
        discover_skill_candidates(root, source="drop", skip_helpers=True)
    )
    if not candidates:
        raise ValueError("dropped folder has no SKILL.md")
    return install_candidates(candidates, on_conflict=on_conflict)


def install_zip(data: bytes, *, on_conflict: str = "ask") -> InstallOutcome:
    with tempfile.TemporaryDirectory(prefix="peon-skill-zip-") as tmp:
        dest_root = extract_zip_safe(data, Path(tmp))
        return install_directory(dest_root, on_conflict=on_conflict)


def install_relative_files(
    files: list[tuple[str, bytes]], *, on_conflict: str = "ask"
) -> InstallOutcome:
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
        return install_directory(root, on_conflict=on_conflict)
