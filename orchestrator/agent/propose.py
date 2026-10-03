"""LLM specialist proposals (no hardcoded technique menus).

Used when an agent calls ``propose_agents``. Spawning stays in the control plane.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from orchestrator.utils.llm import chat_json, llm_configured


@dataclass(frozen=True)
class AgentSpec:
    """One proposed specialist Job under an objective."""

    title: str
    description: str
    skill_name: str = ""


_PROPOSE_SYSTEM = """You plan parallel specialist agents for ONE engagement objective.
Return JSON only:
{"agents":[{"title":"...","description":"...","skill_name":"..."}]}

Rules:
- Propose 0–N agents only when parallel specialists clearly help; else {"agents":[]}.
- Each agent gets a focused brief (description) derived from the objective context.
- skill_name MUST be empty or one of the allowed skill ids provided (prefer the
  objective's primary skill when unsure).
- Do NOT invent skill names. Do NOT hardcode a fixed technique menu — deduce
  workstreams from the objective, RoE, and evidence summary.
- Prefer fewer sharp specialists over many vague ones (max 4).
- Titles short; descriptions actionable under Rules of Engagement.
"""


def propose_agents(
    *,
    objective_title: str,
    objective_description: str,
    acceptance: str,
    primary_skill: str,
    allowed_skills: list[str],
    context_notes: str = "",
    max_agents: int = 4,
) -> list[AgentSpec]:
    """LLM-deduce specialist Jobs from objective context (empty if not useful)."""
    if not llm_configured():
        return []
    allow = [s.strip() for s in allowed_skills if str(s).strip()]
    primary = (primary_skill or "").strip()
    if primary and primary not in allow:
        allow = [primary, *allow]
    human = (
        f"Primary skill: {primary or '(none)'}\n"
        f"Allowed skill ids: {', '.join(allow) or '(none)'}\n"
        f"Max agents: {max(0, min(8, int(max_agents)))}\n\n"
        f"Objective title: {objective_title}\n"
        f"Description: {objective_description or '(none)'}\n"
        f"Acceptance: {acceptance or '(none)'}\n\n"
        f"Extra context:\n{(context_notes or '(none)')[:4000]}\n"
    )
    try:
        data = chat_json(_PROPOSE_SYSTEM, human)
    except Exception:
        return []
    rows = data.get("agents") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[AgentSpec] = []
    allow_set = set(allow)
    for row in rows[: max(0, min(8, int(max_agents)))]:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()[:255]
        desc = str(row.get("description") or "").strip()[:4000]
        skill = str(row.get("skill_name") or primary or "").strip()
        if skill and allow_set and skill not in allow_set:
            skill = primary
        if not title or not desc:
            continue
        out.append(AgentSpec(title=title, description=desc, skill_name=skill))
    return out


def specs_as_dicts(specs: list[AgentSpec]) -> list[dict[str, Any]]:
    return [
        {"title": s.title, "description": s.description, "skill_name": s.skill_name}
        for s in specs
    ]
