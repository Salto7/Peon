"""Dynamic role↔skill affinity — keyword match, then LLM classification.

Every skill and MCP server gets its role binding from this module, DB-driven
end to end: no sidecar file, no cache, nothing read from disk.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import yaml

from orchestrator.skills.common import SKILL_NAME_RE

logger = logging.getLogger(__name__)

AFFINITY_THRESHOLD = 0.5
_BODY_LIMIT = 2500

_CLASSIFY_SYSTEM = """You assign Peon engagement roles to a catalog item (skill or MCP server).

Return ONLY JSON:
{
  "bindings": [
    {"role_id": "<catalog role id>", "score": 0.0-1.0, "lead": false}
  ]
}

Rules:
- role_id must be one of the provided catalog role ids (never invent ids)
- score is confidence that this role's actual specialty IS the item's
  primary purpose — not merely that the item touches a technique the role
  also uses. A recon/OSINT tool that happens to include a port-scan module
  is not a network scanner just because they share that one technique;
  score that kind of incidental overlap low (well under 0.5), not high
- the role catalog is a closed freeze list — never invent a new role_id
- if none of the provided roles' specialties genuinely match the item's
  primary purpose, return bindings: [] — a human will assign a frozen role;
  the platform will NOT invent a new role
- include every role that reasonably fits; omit or score low the ones that
  only touch the item incidentally
- at most one binding may have lead=true (default lead for that role)
- engagement-pm / analyzer roles only bind items clearly for that lane
- Toolsmith/authoring-only packs may return bindings: []
- MCP servers usually bind to technical roles that would call their tools
- No prose outside JSON
"""

_WORD_RE = re.compile(r"[a-z0-9]+")
_ROLE_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "for",
        "of",
        "or",
        "the",
        "to",
        "with",
        "role",
        "expert",
        "specialist",
    }
)


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
    return tuple(out)


_BRIEF_LIMIT = 300


def _role_briefs() -> list[dict[str, str]]:
    """Catalog roles as the LLM classifier sees them.

    Must carry each role's *real* goal/backstory — a placeholder shaped
    identically for every technical role (e.g. "Technical expert role `x`
    for in-lane engagement work.") gives the classifier nothing to
    discriminate on, which is exactly how a prior version of this function
    let an unrelated technical role win by default.
    """
    from orchestrator.crew.roles.catalog import load_role_catalog

    briefs: list[dict[str, str]] = []
    for role in load_role_catalog():
        kind = role.kind
        if kind == "engagement-pm":
            goal = "Engagement lead; dispatch specialists; no probing."
        elif kind == "analyzer":
            goal = "Turn workspace evidence into findings and report.md."
        else:
            goal = f"{role.goal}\n{role.backstory}".strip()
            if not goal:
                goal = f"Technical expert role `{role.id}` for in-lane engagement work."
        if len(goal) > _BRIEF_LIMIT:
            goal = goal[:_BRIEF_LIMIT].rstrip() + "…"
        briefs.append({"id": role.id, "kind": kind, "goal": goal})
    return briefs


def _score_roles(catalog: Iterable[Any], *, name: str) -> tuple[AffinityBinding, ...]:
    """Strict lexical role match: every role-id/alias token must appear in
    the skill's own *name*.

    Deliberately narrow — this is a fast path for a skill that is quite
    literally named after the role (``network-scanner-v2`` -> role
    ``network-scanner``), not a general "shares a word with" heuristic. A
    single shared English word between an OSINT skill's description and a
    role id like ``network-scanner`` ("...OSINT **scanner**...") is not a
    binding signal — matching on description or body text produced exactly
    that kind of false positive, silently mis-assigning skills to unrelated
    roles instead of asking the LLM classifier (or drafting a new role when
    nothing really fits). Anything short of a full name match falls through
    to :func:`_classify_text_affinity`, which has the actual context needed
    to discriminate.
    """
    name_words = set(_WORD_RE.findall(name.lower()))
    bindings: list[AffinityBinding] = []
    for role in catalog:
        tokens = {
            token
            for label in (role.id, *role.aliases)
            for token in _WORD_RE.findall(label.lower())
            if token not in _ROLE_STOPWORDS
        }
        if tokens and tokens <= name_words:
            bindings.append(AffinityBinding(role_id=role.id, score=0.95, lead=True))
    return tuple(bindings)


def _keyword_role_bindings(*, name: str) -> SkillAffinity:
    """Fast lexical role match from the skill's own name (no LLM)."""
    try:
        from orchestrator.crew.roles.catalog import load_role_catalog

        catalog = tuple(load_role_catalog())
    except Exception:
        return SkillAffinity()
    bindings = _score_roles(catalog, name=str(name or ""))
    return SkillAffinity(bindings=bindings, source="dynamic-keyword")


def _classify_text_affinity(
    *,
    owner_id: str,
    owner_kind: str,
    description: str = "",
    body: str = "",
    known_roles: Iterable[str] | None = None,
) -> SkillAffinity:
    """LLM-classify role bindings from catalog text. Empty on failure."""
    from orchestrator.utils.llm import chat_json, llm_configured

    if not llm_configured():
        return SkillAffinity()
    briefs = _role_briefs()
    if known_roles is not None:
        allow = {str(r).strip().lower() for r in known_roles if str(r).strip()}
        briefs = [b for b in briefs if b["id"] in allow]
    if not briefs:
        return SkillAffinity()
    text = (body or "").strip()
    if len(text) > _BODY_LIMIT:
        text = text[:_BODY_LIMIT].rstrip() + "\n…"
    human = (
        f"Item kind: {owner_kind}\n"
        f"Item id: {owner_id}\n"
        f"Description: {description}\n\n"
        f"Details:\n{text}\n\n"
        f"Catalog roles:\n{yaml.safe_dump(briefs, sort_keys=False)}"
    )
    try:
        from orchestrator.utils import llm_usage

        with llm_usage.purpose("skill_affinity"):
            data = chat_json(_CLASSIFY_SYSTEM, human)
    except Exception as exc:
        logger.warning("affinity classify failed for %s: %s", owner_id, exc)
        return SkillAffinity()
    if not isinstance(data, dict):
        return SkillAffinity()
    allowed = {b["id"] for b in briefs}
    raw_bindings = _parse_bindings(data.get("bindings"))
    kept = tuple(b for b in raw_bindings if b.role_id in allowed)
    return SkillAffinity(bindings=kept, source="llm")


def infer_catalog_bindings(
    *,
    owner_id: str,
    owner_kind: str,
    description: str = "",
    body: str = "",
    tags: Iterable[str] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Auto role bindings for catalog save. Keyword first, LLM if empty.

    Returns ``(applied, candidates)``: ``applied`` scored at or above
    ``AFFINITY_THRESHOLD`` and is safe to bind automatically. ``candidates``
    scored above zero but below threshold — too weak to trust unattended,
    but real enough to put in front of a human instead of discarding
    (callers may stage these for review rather than just logging them).
    """
    keyed = _keyword_role_bindings(name=owner_id)
    affinity = keyed
    if not affinity.bindings:
        tag_text = " ".join(str(t) for t in tags)
        affinity = _classify_text_affinity(
            owner_id=owner_id,
            owner_kind=owner_kind,
            description=f"{description} {tag_text}".strip(),
            body=body or description,
        )
    from orchestrator.crew.roles.catalog import FROZEN_ROLE_IDS, resolve_role_id

    def _frozen_row(b: AffinityBinding, *, lead: bool) -> dict[str, Any] | None:
        rid = resolve_role_id(b.role_id)
        if rid not in FROZEN_ROLE_IDS:
            return None
        return {"role_id": rid, "score": b.score, "lead": lead}

    applied = [
        row
        for b in affinity.bindings
        if float(b.score) >= AFFINITY_THRESHOLD
        for row in (_frozen_row(b, lead=bool(b.lead)),)
        if row is not None
    ]
    candidates = [
        row
        for b in affinity.bindings
        if float(b.score) < AFFINITY_THRESHOLD
        for row in (_frozen_row(b, lead=False),)
        if row is not None
    ]
    # The LLM classifier is told "engagement-pm / analyzer roles only bind
    # items clearly for that lane" but does not always follow it — a security
    # tool's own vocabulary ("report", "monitor", "record") can read as a
    # confident match for the manager/analyzer bookends even though neither
    # role can ever call a skill/MCP tool (TECHNICAL_RUNTIME_TOOLS is
    # technical-only). Never auto-bind a bookend with that confidence; demote
    # it to a weak candidate so a human sees and decides, same as any other
    # below-threshold match.
    bookend_kinds = {
        role.id: role.kind for role in _catalog_roles() if role.kind in {"engagement-pm", "analyzer"}
    }
    if bookend_kinds:
        demoted = [row for row in applied if row["role_id"] in bookend_kinds]
        if demoted:
            applied = [row for row in applied if row["role_id"] not in bookend_kinds]
            for row in demoted:
                candidates.append({**row, "lead": False})
    # Catalog allows at most one lead across the whole owner.
    leads = [row for row in applied if row["lead"]]
    if len(leads) > 1:
        winner = max(leads, key=lambda row: (float(row["score"]), row["role_id"]))
        for row in applied:
            row["lead"] = row is winner
    return applied, candidates


def _catalog_roles():
    from orchestrator.crew.roles.catalog import load_role_catalog

    return load_role_catalog()

