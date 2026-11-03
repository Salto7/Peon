"""Skill-vs-skill overlap detection — do two role-mates solve the same job?

This is a different question from ``orchestrator.skills.affinity`` (which role
a skill belongs to) and must not share its keyword/LLM call: a tuning change
for role-matching should never silently change overlap results, or vice
versa. Keyword match first (cheap, no false negatives on exact rewording);
the LLM call only runs to confirm a keyword hit, never to find one on its own.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_KEYWORD_OVERLAP_THRESHOLD = 0.6
_WORD_RE = re.compile(r"[a-z][a-z-]{3,}")
_STOPWORDS = frozenset(
    {
        "this",
        "that",
        "with",
        "from",
        "into",
        "when",
        "skill",
        "using",
        "role",
        "should",
        "which",
        "there",
        "their",
        "where",
        "about",
        "being",
        "these",
        "those",
        "would",
        "could",
        "other",
        "every",
    }
)

_OVERLAP_SYSTEM = """You compare two Peon skills bound to the same engagement role.

Return ONLY JSON: {"overlap": true|false, "reason": "<one sentence>"}

"overlap": true only when a planner handed both skills for the same task
would struggle to tell them apart — same job, same inputs/outputs. Two
skills that are merely in the same general area (e.g. both do network
recon, but one is a port scanner and the other is a DNS enumerator) are
NOT an overlap. When unsure, answer false.
"""


@dataclass(frozen=True)
class SkillOverlap:
    related_skill_id: str
    reason: str


def find_overlap(skill_id: str, corpus: str, siblings: dict[str, str]) -> SkillOverlap | None:
    """``siblings`` maps another skill's id to its own description+body text.

    Only ever called for skills bound to the same role as ``skill_id``.
    Cheapest check first: no LLM call unless a keyword match is already
    plausible.
    """
    best_id, best_score = _best_keyword_match(corpus, siblings)
    if best_id is None or best_score < _KEYWORD_OVERLAP_THRESHOLD:
        return None
    reason = _confirm_with_llm(skill_id, corpus, best_id, siblings[best_id])
    if reason is None:
        return None
    return SkillOverlap(related_skill_id=best_id, reason=reason)


def _best_keyword_match(corpus: str, siblings: dict[str, str]) -> tuple[str | None, float]:
    words = _keywords(corpus)
    if not words:
        return None, 0.0
    best_id, best_score = None, 0.0
    for sibling_id, text in siblings.items():
        other = _keywords(text)
        if not other:
            continue
        score = len(words & other) / len(words | other)
        if score > best_score:
            best_id, best_score = sibling_id, score
    return best_id, best_score


def _keywords(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower())) - _STOPWORDS


def _confirm_with_llm(skill_id: str, corpus: str, other_id: str, other_corpus: str) -> str | None:
    from orchestrator.utils.llm import chat_json, llm_configured

    fallback = f"Keyword overlap with {other_id!r} — review manually."
    if not llm_configured():
        return fallback
    human = (
        f"Skill A ({skill_id}):\n{corpus[:2000]}\n\nSkill B ({other_id}):\n{other_corpus[:2000]}"
    )
    try:
        from orchestrator.utils import llm_usage

        with llm_usage.purpose("skill_overlap"):
            data = chat_json(_OVERLAP_SYSTEM, human)
    except Exception as exc:
        logger.warning("overlap check failed for %s vs %s: %s", skill_id, other_id, exc)
        return fallback
    if not isinstance(data, dict) or not data.get("overlap"):
        return None
    return str(data.get("reason") or fallback)
