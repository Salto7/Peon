"""Skill helpers: catalog rules + path/ref utilities.

Catalog aliases/categories live on each SKILL.md; this module only normalizes
slugs, names cores, and resolves scripts/assets/references paths.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# --- catalog rules -----------------------------------------------------------

# Always-protected platform skill ids (also set metadata.protected on the skill).
CORE_SKILLS = frozenset({"blueprint", "project-manager", "analyzer"})
# Ordered bookends for every project plan / job skill list (not the full CORE set).
PROJECT_PLAN_START = "blueprint"
PROJECT_PLAN_END = "analyzer"

# agentskills.io skill name / category slug shape.
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

SKILL_LIFECYCLES = frozenset({"short", "long", "continuous"})
# Lint also accepts planner "auto" before coerce to a concrete lifecycle.
LINT_LIFECYCLES = frozenset({"short", "long", "continuous", "auto"})

# --- path / manifest ---------------------------------------------------------

RESOURCE_ROOTS = ("scripts", "assets", "references")
SKILL_MANIFEST = "SKILL.md"
SKILL_SUFFIX = ".md"
MANIFEST_NAMES = (SKILL_MANIFEST, "skill.md")
SPEC_FIELDS = frozenset(
    {"name", "description", "license", "compatibility", "allowed-tools", "metadata"}
)
MAX_NAME_LEN, MAX_DESCRIPTION_LEN, MAX_COMPATIBILITY_LEN = 64, 1024, 500

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)", re.DOTALL)
REF_RE = re.compile(
    r"\]\(((?:scripts|assets|references)/[^)\s]+)\)"
    r"|`((?:scripts|assets|references)/[^`]+)`"
    r"|(?:^|\s)((?:scripts|assets|references)/[\w./-]+)",
    re.MULTILINE,
)
KEY_LINE_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:(.*)$")


def resolve_alias(name: str, *, aliases: dict[str, str] | None = None) -> str:
    """Return canonical skill id using the registry alias map (from SKILL.md)."""
    key = (name or "").strip()
    if not key:
        return ""
    if aliases and key in aliases:
        return aliases[key]
    return key


def normalize_category(value: object) -> str:
    """Slug ``metadata.category`` from SKILL.md; empty if unset/invalid."""
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    while "--" in text:
        text = text.replace("--", "-")
    if text and SKILL_NAME_RE.match(text):
        return text
    return ""


def is_protected(*, name: str = "", protected: bool = False) -> bool:
    """True when the skill sets protected or is a known core id."""
    return bool(protected or (name or "").strip() in CORE_SKILLS)


@dataclass(frozen=True)
class LintIssue:
    level: str
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def find_manifest(skill_dir: Path) -> Path | None:
    for name in MANIFEST_NAMES:
        path = skill_dir / name
        if path.is_file():
            return path
    return None


def split_frontmatter(text: str) -> tuple[str, str] | None:
    m = FRONTMATTER_RE.match((text or "").strip())
    return (m.group(1), m.group(2) or "") if m else None


def extract_file_refs(text: str) -> list[str]:
    refs: list[str] = []
    for m in REF_RE.finditer(text or ""):
        ref = next((g for g in m.groups() if g), None)
        if not ref:
            continue
        # Prose often ends with ``scripts/run.py.`` — drop trailing punctuation.
        cleaned = ref.strip().rstrip(".,;:)")
        if cleaned and cleaned not in refs:
            refs.append(cleaned)
    return refs


def list_resource_files(skill_dir: Path) -> list[str]:
    out: list[str] = []
    for root_name in RESOURCE_ROOTS:
        root = skill_dir / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name.startswith(".") or "__pycache__" in path.parts:
                continue
            if path.suffix in {".pyc", ".pyo"}:
                continue
            out.append(path.relative_to(skill_dir).as_posix())
    return out


def resolve_resource(skill_dir: Path, relative_path: str) -> Path | None:
    if not relative_path or relative_path.startswith("/"):
        return None
    rel = relative_path.lstrip("./")
    candidates = [skill_dir / rel]
    for root_name in RESOURCE_ROOTS:
        prefix = f"{root_name}/"
        candidates.append(
            skill_dir / root_name / rel[len(prefix) :]
            if rel.startswith(prefix)
            else skill_dir / root_name / rel
        )
    if rel.startswith("assets/"):
        candidates.append(skill_dir / "scripts" / rel.removeprefix("assets/"))
    if rel.startswith("scripts/"):
        candidates.append(skill_dir / "assets" / rel.removeprefix("scripts/"))
    base = skill_dir.resolve()
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if str(resolved).startswith(str(base)) and resolved.is_file():
            return resolved
    return None
