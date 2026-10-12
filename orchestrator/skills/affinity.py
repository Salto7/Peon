"""Dynamic role↔skill affinity. Persisted affinity remains an optional cache.

Native packs: ``peon.affinity.yaml`` next to SKILL.md.
Imported packs receive role bindings dynamically from their manifest content.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from orchestrator.skills.common import SKILL_NAME_RE
from orchestrator.skills.manifest import ParseProfile, SkillParseError, find_skill_manifest
from orchestrator.skills.model import parse_skill_md
from orchestrator.skills.providers.base import AFFINITY_NAME

logger = logging.getLogger(__name__)

AFFINITY_THRESHOLD = 0.5
_BODY_LIMIT = 2500

_CLASSIFY_SYSTEM = """You assign Peon engagement roles to a skill pack.

Return ONLY JSON:
{
  "bindings": [
    {"role_id": "<catalog role id>", "score": 0.0-1.0, "lead": false}
  ]
}

Rules:
- role_id must be one of the provided catalog role ids (never invent ids)
- score is confidence that this role should own/run the skill (0-1)
- include every role that reasonably fits; omit roles that do not
- at most one binding may have lead=true (the default lead skill for that role)
- engagement-pm / analyzer roles only bind skills that are clearly for that lane
- Learn/authoring-only packs (skill-writer, tools-suggestor) may return bindings: []
- No prose outside JSON
"""


@dataclass(frozen=True)
class AffinityBinding:
    role_id: str
    score: float = 1.0
    lead: bool = False


@dataclass(frozen=True)
class SkillAffinity:
    bindings: tuple[AffinityBinding, ...] = ()
    source: str = ""

    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(b.role_id for b in self.bindings)

    @property
    def lead_roles(self) -> tuple[str, ...]:
        return tuple(b.role_id for b in self.bindings if b.lead)

    def roles_above(self, threshold: float = AFFINITY_THRESHOLD) -> tuple[str, ...]:
        return tuple(
            b.role_id for b in self.bindings if float(b.score) >= float(threshold)
        )


def affinity_path(skill_dir: Path) -> Path:
    return Path(skill_dir) / AFFINITY_NAME


def _clean_role(raw: object) -> str:
    role = str(raw or "").strip().lower()
    if role and SKILL_NAME_RE.match(role):
        return role
    return ""


def _parse_bindings(raw: Any) -> tuple[AffinityBinding, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[AffinityBinding] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            role = _clean_role(item)
            if not role or role in seen:
                continue
            seen.add(role)
            out.append(AffinityBinding(role_id=role, score=1.0, lead=False))
            continue
        if not isinstance(item, dict):
            continue
        role = _clean_role(item.get("role_id") or item.get("role") or item.get("id"))
        if not role or role in seen:
            continue
        try:
            score = float(item.get("score", 1.0))
        except (TypeError, ValueError):
            score = 1.0
        score = max(0.0, min(1.0, score))
        lead = bool(item.get("lead"))
        seen.add(role)
        out.append(AffinityBinding(role_id=role, score=score, lead=lead))
    # At most one lead overall is fine; multiple leads across roles OK.
    return tuple(out)


def read_affinity(skill_dir: Path) -> SkillAffinity:
    path = affinity_path(skill_dir)
    if not path.is_file():
        return SkillAffinity()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return SkillAffinity()
    if not isinstance(data, dict):
        return SkillAffinity()
    return SkillAffinity(
        bindings=_parse_bindings(data.get("bindings") or data.get("roles")),
        source=str(data.get("source") or "").strip(),
    )


def _role_briefs() -> list[dict[str, str]]:
    from orchestrator.crew.roles.catalog import load_role_catalog

    briefs: list[dict[str, str]] = []
    for role in load_role_catalog():
        kind = role.kind
        if kind == "engagement-pm":
            goal = "Engagement lead; dispatch specialists; no probing."
        elif kind == "analyzer":
            goal = "Turn workspace evidence into findings and report.md."
        else:
            goal = f"Technical expert role `{role.id}` for in-lane engagement work."
        briefs.append({"id": role.id, "kind": kind, "goal": goal})
    return briefs


def classify_skill_affinity(
    skill_dir: Path,
    *,
    known_roles: Iterable[str] | None = None,
) -> SkillAffinity:
    """LLM-classify role bindings for a pack. Returns empty affinity on failure."""
    from orchestrator.utils.llm import chat_json, llm_configured

    root = Path(skill_dir)
    manifest = find_skill_manifest(root)
    if manifest is None or not llm_configured():
        return SkillAffinity()
    try:
        spec = parse_skill_md(manifest, profile=ParseProfile.CLAUDE_CODE)
    except SkillParseError as exc:
        logger.debug("affinity parse failed for %s: %s", root, exc)
        return SkillAffinity()
    except OSError as exc:
        logger.debug("affinity read failed for %s: %s", root, exc)
        return SkillAffinity()
    briefs = _role_briefs()
    if known_roles is not None:
        allow = {str(r).strip().lower() for r in known_roles if str(r).strip()}
        briefs = [b for b in briefs if b["id"] in allow]
    if not briefs:
        return SkillAffinity()
    body = (spec.instructions or "").strip()
    if len(body) > _BODY_LIMIT:
        body = body[:_BODY_LIMIT].rstrip() + "\n…"
    human = (
        f"Skill id: {spec.id}\n"
        f"Description: {spec.description}\n\n"
        f"Instructions:\n{body}\n\n"
        f"Catalog roles:\n{yaml.safe_dump(briefs, sort_keys=False)}"
    )
    try:
        data = chat_json(_CLASSIFY_SYSTEM, human)
    except Exception as exc:
        logger.warning("affinity classify failed for %s: %s", spec.id, exc)
        return SkillAffinity()
    if not isinstance(data, dict):
        return SkillAffinity()
    allowed = {b["id"] for b in briefs}
    raw_bindings = _parse_bindings(data.get("bindings"))
    kept = tuple(b for b in raw_bindings if b.role_id in allowed)
    return SkillAffinity(bindings=kept, source="llm")

