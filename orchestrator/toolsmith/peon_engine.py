"""Peon's own Toolsmith engine — plain ``chat_json``, no lab container.

The default engine: works with just an LLM configured, no LiteLLM proxy, no
Docker. See ``peon.projects.opencode.OpenCodeEngine`` for the other one (a
real coding agent, Toolsmith-lab-only) and ``orchestrator.toolsmith.engine
.CodeWritingEngine`` for the shared contract/prompts both implement.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import yaml

from orchestrator.skills.provision import SkillLinter
from orchestrator.toolsmith.draft import finalize_skill_payload, skill_result_dict, valid_skill_name
from orchestrator.toolsmith.engine import CodeWritingEngine
from orchestrator.utils import llm_usage
from orchestrator.utils.llm import chat_json, require_llm
from orchestrator.utils.service import SharedServiceBase


class PeonAuthoring(CodeWritingEngine, SharedServiceBase):
    """``CodeWritingEngine`` backed by Peon's own LLM plumbing directly."""

    engine_id = "peon"

    def _run(self, *, kind: str, system: str, human: str) -> dict[str, Any]:
        require_llm()
        with llm_usage.purpose(f"toolsmith_{kind.replace('-', '_')}"):
            payload = chat_json(system, human)
        if not isinstance(payload, dict):
            raise RuntimeError(f"{kind} authoring returned non-object JSON")
        return payload

    def _finish_skill(self, payload: dict[str, Any], **context: Any) -> dict[str, Any]:
        prompt = context["prompt"]
        system = context["system"]
        human = context["human"]
        proposed = context["proposed"]
        install_recipes = context["install_recipes"]
        tool_suggestion = context["tool_suggestion"]

        name, skill_md, files, suggested, mode, lint = finalize_skill_payload(
            payload, prompt=prompt, proposed=proposed, install_recipes=install_recipes
        )
        if not lint["compatible"]:
            # One repair pass: hand the linter's own errors back and ask again.
            with llm_usage.purpose("toolsmith_skill_repair"):
                repair = chat_json(
                    system,
                    f"{human}\n\n"
                    f"The previous draft failed skills-ref:\n"
                    f"{yaml.safe_dump(lint['errors'], sort_keys=False)}\n\n"
                    "Return corrected JSON only for the stated authoring mode.",
                )
            if isinstance(repair, dict):
                try:
                    name, skill_md, files, suggested, mode, lint = finalize_skill_payload(
                        repair,
                        prompt=prompt,
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
            author=self.engine_id,
        )


def lint_skill(*, name: str, skill_md: str, files: dict[str, str] | None = None) -> dict[str, Any]:
    """Lint editable skill content (no LLM) — not an engine concern, just a utility
    Toolsmith's own editor flow and the catalog editor's skill-save flow both use."""
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
