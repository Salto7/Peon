"""Learn authoring helpers — chat_json path for role packs / offline drafts."""

from __future__ import annotations

from typing import Any

from orchestrator.crew.roles.registry import RoleRegistry
from orchestrator.learn.draft import (
    authoring_prompt,
    catalog_summaries,
    filter_catalog,
    propose_missing_tool,
    tool_replan_human,
    tool_suggest_human,
    tool_suggestion_from_payload,
)
from orchestrator.learn.role_draft import assemble_role_result, lint_role_pack
from orchestrator.utils.llm import chat_json, require_llm
from orchestrator.utils.service import SharedServiceBase

_DEFAULT_AUTHOR = "code-writer"


def _authoring_role(role_id: str = "") -> Any:
    rid = (role_id or _DEFAULT_AUTHOR).strip() or _DEFAULT_AUTHOR
    role = RoleRegistry.shared().get(rid)
    if role is None:
        raise RuntimeError(f"authoring role {rid!r} not found in roles/")
    return role


class LearnAuthoring(SharedServiceBase):
    """LLM authoring via chat_json (no OpenCode / proxy lab)."""

    @staticmethod
    def suggest_tool(prompt: str, *, role_id: str = "") -> dict[str, Any]:
        text = (prompt or "").strip()
        if not text:
            raise ValueError("prompt is required")
        require_llm()
        role = _authoring_role(role_id)
        return tool_suggestion_from_payload(
            chat_json(role.authoring_prompt_text("tool"), tool_suggest_human(text))
        )

    @staticmethod
    def write_role(
        prompt: str, *, tools: list[str] | None = None, role_id: str = ""
    ) -> dict[str, Any]:
        text = (prompt or "").strip()
        if not text:
            raise ValueError("prompt is required")
        require_llm()
        role = _authoring_role(role_id)
        tool_suggestion, proposed, install_recipes = propose_missing_tool(
            text, LearnAuthoring.suggest_tool
        )
        return assemble_role_result(
            chat_json(
                role.authoring_prompt_text("role"),
                authoring_prompt(
                    text,
                    filter_catalog(catalog_summaries(), tools),
                    proposed=proposed,
                    tool_recipes=install_recipes or None,
                ),
            ),
            prompt=text,
            author=role.id,
            tool_suggestion=tool_suggestion,
        )

    @staticmethod
    def replan_tool(
        *,
        prompt: str,
        yaml_text: str,
        install_script: str = "",
        error: str = "",
        feedback: str = "",
        role_id: str = "",
    ) -> dict[str, Any]:
        require_llm()
        role = _authoring_role(role_id)
        return tool_suggestion_from_payload(
            chat_json(
                role.authoring_prompt_text("tool_replan"),
                tool_replan_human(
                    prompt=prompt,
                    yaml_text=yaml_text,
                    install_script=install_script,
                    error=error,
                    feedback=feedback,
                ),
            )
        )

    @staticmethod
    def lint_role(
        *,
        name: str,
        role_yaml: str = "",
        files: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Lint a role pack before Learn save."""
        return lint_role_pack(
            name=name,
            role_yaml=(role_yaml or "").strip(),
            files=files,
        )
