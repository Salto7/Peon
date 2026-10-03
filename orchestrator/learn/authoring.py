"""Learn authoring — tool YAML suggestions and skill scaffolding."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import yaml

from orchestrator.learn.draft import (
    filter_catalog,
    finalize_skill_payload,
    catalog_summaries,
    authoring_prompt,
    propose_missing_tool,
    skill_prompt,
    skill_result_dict,
    tool_replan_human,
    tool_suggest_human,
    tool_suggestion_from_payload,
    valid_skill_name,
)
from orchestrator.skills.provision import SkillLinter
from orchestrator.utils.llm import require_llm, chat_json
from orchestrator.utils.service import SharedServiceBase

_TOOLS_SUGGESTOR = "tools-suggestor"
_SKILL_WRITER = "skill-writer"


class LearnAuthoring(SharedServiceBase):
    """LLM authoring backed by skill-writer / tools-suggestor prompt files.

    Methods are static — no instance state; ``shared()`` remains for DI/tests.
    """

    @staticmethod
    def suggest_tool(prompt: str) -> dict[str, Any]:
        """Return ``id``, ``yaml``, ``install_script``, ``notes`` for a catalog tool."""
        text = (prompt or "").strip()
        if not text:
            raise ValueError("prompt is required")
        require_llm()
        return tool_suggestion_from_payload(
            chat_json(skill_prompt(_TOOLS_SUGGESTOR), tool_suggest_human(text))
        )

    @staticmethod
    def write_skill(
        prompt: str, *, tools: list[str] | None = None
    ) -> dict[str, Any]:
        """Draft SKILL.md + files; return lint compatibility summary.

        Missing catalog CLI → tools-suggestor recipe → ``references/INSTALL.md``
        (InstallResolver #2) + ``run_binary`` wrapper.
        """
        text = (prompt or "").strip()
        if not text:
            raise ValueError("prompt is required")
        require_llm()
        catalog = filter_catalog(catalog_summaries(), tools)

        tool_suggestion, proposed, install_recipes = propose_missing_tool(
            text, LearnAuthoring.suggest_tool
        )

        system = skill_prompt(_SKILL_WRITER)
        human = authoring_prompt(
            text,
            catalog,
            proposed=proposed,
            tool_recipes=install_recipes or None,
        )

        payload = chat_json(system, human)
        if not isinstance(payload, dict):
            raise RuntimeError("skill-writer returned non-object JSON")

        name, skill_md, files, suggested, mode, lint = finalize_skill_payload(
            payload,
            prompt=text,
            proposed=proposed,
            install_recipes=install_recipes,
        )

        if not lint["compatible"]:
            repair = chat_json(
                system,
                (
                    f"{human}\n\n"
                    f"The previous draft failed SkillLinter:\n"
                    f"{yaml.safe_dump(lint['errors'], sort_keys=False)}\n\n"
                    "Return corrected JSON only for the stated authoring mode."
                ),
            )
            if isinstance(repair, dict):
                try:
                    name, skill_md, files, suggested, mode, lint = finalize_skill_payload(
                        repair,
                        prompt=text,
                        proposed=proposed,
                        install_recipes=install_recipes,
                        suggested=suggested,
                    )
                except RuntimeError:
                    pass

        return skill_result_dict(
            name=name,
            skill_md=skill_md,
            files=files,
            notes=str(payload.get("notes") or "").strip(),
            suggested=suggested,
            mode=mode,
            lint=lint,
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
    ) -> dict[str, Any]:
        """Revise a tool recipe after a failed learn-lab install test."""
        require_llm()
        return tool_suggestion_from_payload(
            chat_json(
                skill_prompt(_TOOLS_SUGGESTOR),
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
    def lint_skill(
        *, name: str, skill_md: str, files: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Lint editable skill content (no LLM)."""
        nm = (name or "").strip().lower()
        md = (skill_md or "").strip()
        file_map = {str(k): str(v) for k, v in (files or {}).items()}
        if not valid_skill_name(nm):
            raise ValueError(f"invalid skill name {nm!r}")
        if not md:
            raise ValueError("skill_md is required")
        with tempfile.TemporaryDirectory(prefix=f"peon-lint-{nm}-") as tmp:
            skill_dir = Path(tmp) / nm
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(md + "\n", encoding="utf-8")
            for rel, body in file_map.items():
                path = skill_dir / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(body).rstrip() + "\n", encoding="utf-8")
            return SkillLinter.check(skill_dir)
