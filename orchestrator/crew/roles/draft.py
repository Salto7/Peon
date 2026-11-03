"""Draft a dedicated role (and, for an MCP server, its companion skill) for
a catalog item that no existing role fits.

Called synchronously from ``PlatformCatalog._resolve_role_for_owner`` the
moment a skill or MCP install's auto-bind scores every role below
confidence — never blocks the install itself (LLM failure/timeout falls
back to a deterministic template; nothing here raises).
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_BODY_LIMIT = 6000

_SYSTEM_MCP = """You draft a Peon engagement role + skill pack for one newly installed MCP \
server that no existing role matched confidently.

Output ONLY a JSON object:
{
  "role_id": "short-kebab-case-id ending in -expert",
  "goal": "one sentence: what this role authorizes/leads",
  "backstory": "1-2 sentences, second person ('You lead...'), authorized/RoE-bound tone",
  "skill_description": "one sentence for the skill's own frontmatter description",
  "skill_body": "the SKILL.md body in Markdown, starting with '## Goal' — NOT the frontmatter"
}

Ground every field in the server's actual description/README text given below —
never invent capabilities it does not describe. The skill body must tell the
agent to work through the MCP tool-call surface: \
`list_mcp_tools("<mcp_id>")` once, then `call_mcp_tool("<mcp_id>", "<tool>", "<json args>")` \
per step, reading each result before picking the next tool. If the text shows \
an initial per-session setup call (e.g. something like set_engagement/configure/init), \
say to call it first and never repeat whatever secret it takes — fetch it via \
`get_credential` and never put it in a message/finding/memory note. Stay inside \
RoE: only in-scope subjects, authorized use only, no ethics lectures. No prose \
outside the JSON."""

_SYSTEM_SKILL = """You draft a Peon engagement role for one skill pack that no \
existing role matched confidently.

Output ONLY a JSON object:
{
  "role_id": "short-kebab-case-id ending in -expert",
  "goal": "one sentence: what this role authorizes/leads",
  "backstory": "1-2 sentences, second person ('You lead...'), authorized/RoE-bound tone"
}

Ground every field in the skill's own description/body text given below —
never invent capabilities it does not describe. Stay inside RoE: only
in-scope subjects, authorized use only, no ethics lectures. No prose outside
the JSON."""


def _role_id_for(owner_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", owner_id.lower()).strip("-") or "item"
    return f"{slug}-expert"


def _fallback(owner_kind: str, owner_id: str, description: str) -> dict[str, Any]:
    """Deterministic draft when no LLM is configured or the call fails."""
    role_id = _role_id_for(owner_id)
    desc = (description or f"the {owner_id} {owner_kind}").strip()
    draft: dict[str, Any] = {
        "role_id": role_id,
        "goal": f"Lead authorized work with {desc} within Rules of Engagement.",
        "backstory": (
            f"You lead authorized engagement work through {owner_id}. "
            "You stay inside the Rules of Engagement and never act outside in-scope subjects."
        ),
    }
    if owner_kind != "mcp":
        return draft
    draft["skill_description"] = f"Use the {owner_id} MCP server. {desc}"
    draft["skill_body"] = (
        f"## Goal\nDrive the `{owner_id}` MCP server toward the objective, inside RoE.\n\n"
        "## Workflow\n"
        f'1. `list_mcp_tools("{owner_id}")` once per job to see the live tool surface.\n'
        "2. If a one-time session/setup call exists (e.g. anything named like "
        "`set_engagement`/`configure`/`init`), call it first with target + "
        "`get_credential` values — never pass a secret again after that.\n"
        f'3. `call_mcp_tool("{owner_id}", "<tool>", "<json args>")` per step, reading '
        "each result before choosing the next tool.\n"
        "4. `record_finding` each proven result as you confirm it.\n\n"
        "## Scope & OPSEC\nOnly in-scope subjects. `get_credential` returns "
        "plaintext — use it only as a tool-call argument, never echo it back."
    )
    return draft


def render_skill_md(mcp_id: str, draft: dict[str, Any]) -> str:
    """An MCP ``draft_role``/``_fallback`` result as a full SKILL.md file."""
    return (
        "---\n"
        f"name: {mcp_id}\n"
        f"description: {draft['skill_description']}\n"
        "allowed-tools: Bash Read\n"
        "---\n\n"
        f"{draft['skill_body']}\n"
    )


def draft_role(
    *, owner_kind: str, owner_id: str, description: str = "", corpus: str = ""
) -> dict[str, Any]:
    """Draft a role for ``owner_id`` (``owner_kind`` is ``"skill"`` or ``"mcp"``).

    For an MCP server, also drafts its companion skill (``skill_description``/
    ``skill_body`` keys) — a skill being evaluated already exists, so the
    skill case only ever drafts the role itself. Never raises; falls back to
    a deterministic template on any failure.
    """
    from orchestrator.utils.llm import chat_json, llm_configured

    if not llm_configured():
        return _fallback(owner_kind, owner_id, description)
    text = (corpus or description or "").strip()
    if len(text) > _BODY_LIMIT:
        text = text[:_BODY_LIMIT].rstrip() + "\n…"
    human = f"{owner_kind} id: {owner_id}\nDescription: {description}\n\nText:\n{text}"
    system = _SYSTEM_MCP if owner_kind == "mcp" else _SYSTEM_SKILL
    try:
        from orchestrator.utils import llm_usage

        with llm_usage.purpose("catalog_role_draft"):
            data = chat_json(system, human)
    except Exception as exc:  # noqa: BLE001
        logger.warning("role draft failed for %s %s: %s", owner_kind, owner_id, exc)
        return _fallback(owner_kind, owner_id, description)
    if not isinstance(data, dict):
        return _fallback(owner_kind, owner_id, description)
    role_id = re.sub(r"[^a-z0-9-]+", "-", str(data.get("role_id") or "").strip().lower()).strip("-")
    goal = str(data.get("goal") or "").strip()
    backstory = str(data.get("backstory") or "").strip()
    if not (role_id and goal and backstory):
        return _fallback(owner_kind, owner_id, description)
    draft: dict[str, Any] = {"role_id": role_id, "goal": goal, "backstory": backstory}
    if owner_kind != "mcp":
        return draft
    skill_body = str(data.get("skill_body") or "").strip()
    if not skill_body:
        return _fallback(owner_kind, owner_id, description)
    draft["skill_description"] = str(data.get("skill_description") or goal).strip()
    draft["skill_body"] = skill_body
    return draft
