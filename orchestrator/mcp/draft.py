"""Draft a dedicated role + skill for an MCP server that no existing role fits.

Called synchronously from ``PlatformCatalog._apply_owner_bindings`` the
moment an MCP install's auto-bind scores every role below confidence —
never blocks the install itself (LLM failure/timeout falls back to a
deterministic template; nothing here raises).
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_BODY_LIMIT = 6000

_SYSTEM = """You draft a Peon engagement role + skill pack for one newly installed MCP \
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


def _fallback(mcp_id: str, description: str) -> dict[str, Any]:
    """Deterministic draft when no LLM is configured or the call fails."""
    slug = re.sub(r"[^a-z0-9]+", "-", mcp_id.lower()).strip("-") or "mcp"
    role_id = f"{slug}-expert"
    desc = (description or f"the {mcp_id} MCP server").strip()
    return {
        "role_id": role_id,
        "goal": f"Lead authorized work with {desc} within Rules of Engagement.",
        "backstory": (
            f"You lead authorized engagement work through the {mcp_id} MCP server. "
            "You stay inside the Rules of Engagement and never act outside in-scope subjects."
        ),
        "skill_description": f"Use the {mcp_id} MCP server. {desc}",
        "skill_body": (
            f"## Goal\nDrive the `{mcp_id}` MCP server toward the objective, inside RoE.\n\n"
            "## Workflow\n"
            f'1. `list_mcp_tools("{mcp_id}")` once per job to see the live tool surface.\n'
            "2. If a one-time session/setup call exists (e.g. anything named like "
            "`set_engagement`/`configure`/`init`), call it first with target + "
            "`get_credential` values — never pass a secret again after that.\n"
            f'3. `call_mcp_tool("{mcp_id}", "<tool>", "<json args>")` per step, reading '
            "each result before choosing the next tool.\n"
            "4. `record_finding` each proven result as you confirm it.\n\n"
            "## Scope & OPSEC\nOnly in-scope subjects. `get_credential` returns "
            "plaintext — use it only as a tool-call argument, never echo it back."
        ),
    }


def render_skill_md(mcp_id: str, draft: dict[str, Any]) -> str:
    """A ``draft_role_and_skill``/``_fallback`` result as a full SKILL.md file."""
    return (
        "---\n"
        f"name: {mcp_id}\n"
        f"description: {draft['skill_description']}\n"
        "allowed-tools: Bash Read\n"
        "---\n\n"
        f"{draft['skill_body']}\n"
    )


def draft_role_and_skill(
    *, mcp_id: str, description: str = "", corpus: str = ""
) -> dict[str, Any]:
    """Draft a role + skill for ``mcp_id``. Never raises; falls back on any failure."""
    from orchestrator.utils.llm import chat_json, llm_configured

    if not llm_configured():
        return _fallback(mcp_id, description)
    text = (corpus or description or "").strip()
    if len(text) > _BODY_LIMIT:
        text = text[:_BODY_LIMIT].rstrip() + "\n…"
    human = f"MCP id: {mcp_id}\nDescription: {description}\n\nServer text:\n{text}"
    try:
        from orchestrator.utils import llm_usage

        with llm_usage.purpose("mcp_role_skill_draft"):
            data = chat_json(_SYSTEM, human)
    except Exception as exc:  # noqa: BLE001
        logger.warning("mcp role/skill draft failed for %s: %s", mcp_id, exc)
        return _fallback(mcp_id, description)
    if not isinstance(data, dict):
        return _fallback(mcp_id, description)
    role_id = re.sub(r"[^a-z0-9-]+", "-", str(data.get("role_id") or "").strip().lower()).strip("-")
    goal = str(data.get("goal") or "").strip()
    backstory = str(data.get("backstory") or "").strip()
    skill_body = str(data.get("skill_body") or "").strip()
    if not (role_id and goal and backstory and skill_body):
        return _fallback(mcp_id, description)
    return {
        "role_id": role_id,
        "goal": goal,
        "backstory": backstory,
        "skill_description": str(data.get("skill_description") or goal).strip(),
        "skill_body": skill_body,
    }
