"""The contract every Toolsmith authoring backend implements.

Every prompt Toolsmith sends an LLM is built exactly once, here — a second
engine (today: ``PeonAuthoring`` below, and ``peon.projects.opencode
.OpenCodeEngine``) never re-specifies what to ask, only how to run it.
``suggest_tool``/``replan_tool`` are fully concrete: both existing engines do
the same two things with them (run one prompt, parse one JSON result), so
there is nothing engine-specific left to override. ``write_skill`` keeps one
abstract step (``_finish_skill``) because turning a draft into a final
result genuinely differs — ``PeonAuthoring`` retries once past a lint
failure; ``OpenCodeEngine`` merges in the real files it wrote and tested —
but the prompt itself is still built once, in ``write_skill`` here.

Adding a third engine (a local model, a different coding agent, …) means
implementing ``engine_id``, ``_run``, and ``_finish_skill`` — nothing about
*what* Toolsmith asks for is that engine's concern.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from orchestrator.toolsmith.draft import (
    authoring_prompt,
    catalog_summaries,
    filter_catalog,
    propose_missing_tool,
    skill_prompt,
    tool_replan_human,
    tool_suggest_human,
    tool_suggestion_from_payload,
)
from orchestrator.toolsmith.system_skills import skill_writer_skill, tools_suggestor_skill

_TOOL_RESULT_HINT = (
    "Write result.json with keys: id, yaml, install_script, notes. "
    "yaml must be a full catalog tool YAML string."
)
_TOOL_REPLAN_RESULT_HINT = "Write result.json with keys: id, yaml, install_script, notes."


class CodeWritingEngine(ABC):
    """One Toolsmith authoring backend. See module docstring for the contract."""

    @property
    @abstractmethod
    def engine_id(self) -> str:
        """Short id: tags results (``author``) and selects this engine from the UI."""

    @abstractmethod
    def _run(self, *, kind: str, system: str, human: str) -> dict[str, Any]:
        """Execute one prompt; return its JSON payload. The only engine-specific step
        for ``suggest_tool``/``replan_tool``, and half of it for ``write_skill``."""

    def suggest_tool(self, prompt: str) -> dict[str, Any]:
        """Return ``id``, ``yaml``, ``install_script``, ``notes`` for a catalog tool."""
        text = (prompt or "").strip()
        if not text:
            raise ValueError("prompt is required")
        payload = self._run(
            kind="tool",
            system=skill_prompt(tools_suggestor_skill()),
            human=f"{tool_suggest_human(text)}\n\n{_TOOL_RESULT_HINT}",
        )
        return tool_suggestion_from_payload(payload, author=self.engine_id)

    def replan_tool(
        self,
        *,
        prompt: str,
        yaml_text: str,
        install_script: str = "",
        error: str = "",
        feedback: str = "",
    ) -> dict[str, Any]:
        """Revise a tool recipe after a failed install test."""
        human = tool_replan_human(
            prompt=prompt,
            yaml_text=yaml_text,
            install_script=install_script,
            error=error,
            feedback=feedback,
        )
        payload = self._run(
            kind="tool-replan",
            system=skill_prompt(tools_suggestor_skill()),
            human=f"{human}\n\n{_TOOL_REPLAN_RESULT_HINT}",
        )
        return tool_suggestion_from_payload(payload, author=self.engine_id)

    def write_skill(self, prompt: str, *, tools: list[str] | None = None) -> dict[str, Any]:
        """Draft a Claude Agent Skill and return the skills-ref summary.

        A catalog CLI becomes ``scripts/run.sh``. A missing CLI still gets
        ``references/INSTALL.md`` from a tool-provisioning draft.
        """
        text = (prompt or "").strip()
        if not text:
            raise ValueError("prompt is required")
        catalog = filter_catalog(catalog_summaries(), tools)
        tool_suggestion, proposed, install_recipes = propose_missing_tool(text, self.suggest_tool)
        system = skill_prompt(skill_writer_skill())
        human = authoring_prompt(
            text, catalog, proposed=proposed, tool_recipes=install_recipes or None
        )
        payload = self._run(kind="skill", system=system, human=human)
        return self._finish_skill(
            payload,
            prompt=text,
            system=system,
            human=human,
            tool_suggestion=tool_suggestion,
            proposed=proposed,
            install_recipes=install_recipes,
        )

    @abstractmethod
    def _finish_skill(self, payload: dict[str, Any], **context: Any) -> dict[str, Any]:
        """Turn one raw payload into the Toolsmith skill result. Engine-specific —
        see the module docstring for why this is the one step that stays abstract."""
