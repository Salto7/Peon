"""Learn authoring — tool YAML suggestions and skill scaffolding."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import yaml

from orchestrator.config import get_config
from orchestrator.learn.draft import (
    authoring_prompt,
    catalog_summaries,
    ensure_skill_entry,
    lint_draft,
    parse_skill_payload,
    prompt_catalog_hits,
    proposed_tool_match,
    valid_skill_name,
)
from orchestrator.skills.provision import SkillLinter
from orchestrator.utils.llm import require_llm, chat_json
from orchestrator.utils.service import SharedService
from orchestrator.prompts import CATALOG_INSTALL_PREFER

_TOOLS_SUGGESTOR = "tools-suggestor"
_SKILL_WRITER = "skill-writer"


def skills_root() -> Path:
    return Path(get_config().skills_dir).resolve()


def _read_prompt(skill_name: str) -> str:
    path = skills_root() / skill_name / "references" / "PROMPT.md"
    if not path.is_file():
        raise FileNotFoundError(f"missing prompt for skill {skill_name}: {path}")
    return path.read_text(encoding="utf-8")


def _tool_suggestion_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("tools-suggestor returned non-object JSON")
    yaml_text = str(payload.get("yaml") or "").strip()
    if not yaml_text:
        raise RuntimeError("tools-suggestor returned empty yaml")
    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise RuntimeError(f"suggested YAML is invalid: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("suggested YAML must be a mapping")
    tid = str(payload.get("id") or parsed.get("id") or "").strip()
    if not tid:
        raise RuntimeError("suggested tool missing id")
    return {
        "id": tid,
        "yaml": yaml_text,
        "install_script": str(payload.get("install_script") or "").strip(),
        "notes": str(payload.get("notes") or "").strip(),
        "parsed": parsed,
    }


class LearnAuthoring(SharedService):
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
        human = (
            f"Operator request:\n{text}\n\n"
            f"Existing catalog tools (avoid duplicate ids):\n"
            f"{yaml.safe_dump(catalog_summaries(), sort_keys=False)}"
        )
        return _tool_suggestion_from_payload(
            chat_json(_read_prompt(_TOOLS_SUGGESTOR), human)
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
        catalog = catalog_summaries()
        if tools:
            wanted = {t.strip().lower() for t in tools if str(t).strip()}
            catalog = [t for t in catalog if t["id"] in wanted] or catalog

        tool_suggestion: dict[str, Any] | None = None
        proposed: list[dict[str, str]] = []
        install_recipes: list[dict[str, Any]] = []
        if not prompt_catalog_hits(text):
            tool_suggestion = LearnAuthoring.suggest_tool(text)
            proposed = [proposed_tool_match(tool_suggestion)]
            install_recipes = [
                {
                    "id": tool_suggestion["id"],
                    "yaml": tool_suggestion["yaml"],
                    "install_script": tool_suggestion.get("install_script") or "",
                    "notes": tool_suggestion.get("notes") or "",
                }
            ]

        system = _read_prompt(_SKILL_WRITER)
        human = authoring_prompt(
            text,
            catalog,
            proposed=proposed,
            tool_recipes=install_recipes or None,
        )

        def _finalize(
            payload: dict[str, Any], *, suggested: list[str]
        ) -> tuple[str, str, dict[str, str], list[str], str, dict[str, Any]]:
            name, skill_md, files, sug = parse_skill_payload(
                {**payload, "suggested_tools": payload.get("suggested_tools") or suggested}
            )
            if proposed and not sug:
                sug = [proposed[0]["id"]]
            skill_md, files, mode = ensure_skill_entry(
                skill_md,
                files,
                name=name,
                suggested_tools=sug,
                prompt=text,
                proposed=proposed,
                install_recipes=install_recipes,
            )
            lint = lint_draft(skill_md, files, mode=mode)
            return name, skill_md, files, sug, mode, lint

        payload = chat_json(system, human)
        if not isinstance(payload, dict):
            raise RuntimeError("skill-writer returned non-object JSON")

        name, skill_md, files, suggested, mode, lint = _finalize(payload, suggested=[])

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
                    name, skill_md, files, suggested, mode, lint = _finalize(
                        repair, suggested=suggested
                    )
                except RuntimeError:
                    pass

        result: dict[str, Any] = {
            "name": name,
            "skill_md": skill_md,
            "files": files,
            "notes": str(payload.get("notes") or "").strip(),
            "suggested_tools": suggested,
            "mode": mode,
            "lint": lint,
        }
        if tool_suggestion is not None:
            result["tool_suggestion"] = {
                "id": tool_suggestion["id"],
                "yaml": tool_suggestion["yaml"],
                "install_script": tool_suggestion.get("install_script") or "",
                "notes": tool_suggestion.get("notes") or "",
            }
        return result

    @staticmethod
    def replan_tool(
        *,
        prompt: str,
        yaml_text: str,
        install_script: str = "",
        error: str = "",
        feedback: str = "",
    ) -> dict[str, Any]:
        """Revise a tool recipe after a failed learn-lab install test.

        Keeps the original operator request plus the failed YAML/script/error so
        the model revises in-context (same authoring turn, not a fresh suggest).
        Optional ``feedback`` is extra operator guidance for this retry.
        """
        text = (prompt or "").strip() or "Revise the catalog install recipe."
        fix = (feedback or "").strip()
        require_llm()
        parts = [
            f"Operator request:\n{text}",
            f"Previous YAML (failed install test):\n```yaml\n{yaml_text}\n```",
            f"Previous install script:\n```bash\n{install_script or '(none)'}\n```",
            f"Install/test error:\n{error or '(unknown)'}",
        ]
        if fix:
            parts.append(f"Operator fix guidance:\n{fix}")
        parts.append(
            "Produce a revised catalog YAML that is more likely to install on "
            "debian:bookworm-slim. Keep the same tool intent as the operator "
            "request; fix install/verify based on the error"
            + (" and fix guidance." if fix else ".")
            + f" {CATALOG_INSTALL_PREFER}\n"
            f"Existing catalog tools:\n{yaml.safe_dump(catalog_summaries(), sort_keys=False)}"
        )
        return _tool_suggestion_from_payload(
            chat_json(_read_prompt(_TOOLS_SUGGESTOR), human="\n\n".join(parts))
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
