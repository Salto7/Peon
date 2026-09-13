"""Learn authoring — tool YAML suggestions and skill scaffolding via catalog skills."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from orchestrator.skills.misc.utils import LintIssue, extract_file_refs
from orchestrator.skills.provision import SkillLinter
from orchestrator.tools.catalog import ToolCatalog
from orchestrator.utils.llm import chat_json, llm_config
from orchestrator.utils.service import SharedService

logger = logging.getLogger(__name__)

_TOOLS_SUGGESTOR = "tools-suggestor"
_SKILL_WRITER = "skill-writer"
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def skills_root() -> Path:
    """``skills/`` directory (repo layout: orchestrator/ → parents[2]/skills)."""
    return Path(__file__).resolve().parents[2] / "skills"


def _read_prompt(skill_name: str) -> str:
    path = skills_root() / skill_name / "references" / "PROMPT.md"
    if not path.is_file():
        raise FileNotFoundError(f"missing prompt for skill {skill_name}: {path}")
    return path.read_text(encoding="utf-8")


def _require_llm() -> None:
    if not (llm_config().api_key or "").strip():
        raise RuntimeError(
            "LLM not configured — set OPENROUTER_API_KEY (or OpenAI/LiteLLM)."
        )


def _catalog_tool_summaries(*, limit: int = 80) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for tool in sorted(ToolCatalog.shared().all().values(), key=lambda t: t.id):
        if tool.is_image_tier:
            continue
        out.append(
            {
                "id": tool.id,
                "binary": tool.binary or tool.id,
                "description": (tool.description or "")[:240],
                "install_types": ",".join(
                    str(s.get("type") or "")
                    for s in (tool.install or [])
                    if isinstance(s, dict)
                ),
            }
        )
        if len(out) >= limit:
            break
    return out


class LearnAuthoring(SharedService):
    """Peon Learn + skill scripts: LLM authoring backed by skill prompt files."""

    def suggest_tool(self, prompt: str) -> dict[str, Any]:
        """Return ``id``, ``yaml``, ``install_script``, ``notes`` for a catalog tool."""
        text = (prompt or "").strip()
        if not text:
            raise ValueError("prompt is required")
        _require_llm()
        system = _read_prompt(_TOOLS_SUGGESTOR)
        human = (
            f"Operator request:\n{text}\n\n"
            f"Existing catalog tools (avoid duplicate ids):\n"
            f"{yaml.safe_dump(_catalog_tool_summaries(), sort_keys=False)}"
        )
        payload = chat_json(system, human)
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

    def write_skill(
        self, prompt: str, *, tools: list[str] | None = None
    ) -> dict[str, Any]:
        """Draft SKILL.md + files; return lint compatibility summary."""
        text = (prompt or "").strip()
        if not text:
            raise ValueError("prompt is required")
        _require_llm()
        catalog = _catalog_tool_summaries()
        if tools:
            wanted = {t.strip().lower() for t in tools if str(t).strip()}
            catalog = [t for t in catalog if t["id"] in wanted] or catalog
        system = _read_prompt(_SKILL_WRITER)
        human = (
            f"Operator request:\n{text}\n\n"
            f"Available catalog tools:\n"
            f"{yaml.safe_dump(catalog, sort_keys=False)}"
        )
        payload = chat_json(system, human)
        if not isinstance(payload, dict):
            raise RuntimeError("skill-writer returned non-object JSON")
        name = str(payload.get("name") or "").strip().lower()
        skill_md = str(payload.get("skill_md") or "").strip()
        files_raw = payload.get("files") or {}
        files = (
            {str(k): str(v) for k, v in files_raw.items()}
            if isinstance(files_raw, dict)
            else {}
        )
        if not name or not _NAME_RE.match(name):
            raise RuntimeError(f"invalid skill name {name!r}")
        if not skill_md:
            raise RuntimeError("skill-writer returned empty skill_md")

        issues = list(SkillLinter.shared().lint_text(skill_md, skill_dir=None))
        known = set(files)
        for ref in extract_file_refs(skill_md):
            if ref not in known:
                issues.append(
                    LintIssue(
                        "error",
                        "broken_ref",
                        f"File reference `{ref}` not found.",
                    )
                )
        errors = [i.to_dict() for i in issues if i.level == "error"]
        warnings = [i.to_dict() for i in issues if i.level == "warning"]
        return {
            "name": name,
            "skill_md": skill_md,
            "files": files,
            "notes": str(payload.get("notes") or "").strip(),
            "suggested_tools": [
                str(t).strip()
                for t in (payload.get("suggested_tools") or [])
                if str(t).strip()
            ],
            "lint": {
                "compatible": not errors,
                "errors": errors,
                "warnings": warnings,
            },
        }

    def replan_tool(
        self,
        *,
        prompt: str,
        yaml_text: str,
        install_script: str = "",
        error: str = "",
    ) -> dict[str, Any]:
        """Revise a tool recipe after a failed Learn-lab install test."""
        text = (prompt or "").strip() or "Revise the catalog install recipe."
        _require_llm()
        system = _read_prompt(_TOOLS_SUGGESTOR)
        human = (
            f"Operator request:\n{text}\n\n"
            f"Previous YAML (failed install test):\n```yaml\n{yaml_text}\n```\n\n"
            f"Previous install script:\n```bash\n{install_script or '(none)'}\n```\n\n"
            f"Install/test error:\n{error or '(unknown)'}\n\n"
            "Produce a revised catalog YAML that is more likely to install on "
            "debian:bookworm-slim. Prefer custom/apt/github_release in that order.\n"
            f"Existing catalog tools:\n{yaml.safe_dump(_catalog_tool_summaries(), sort_keys=False)}"
        )
        payload = chat_json(system, human)
        if not isinstance(payload, dict):
            raise RuntimeError("tools-suggestor replan returned non-object JSON")
        yaml_out = str(payload.get("yaml") or "").strip()
        if not yaml_out:
            raise RuntimeError("replan returned empty yaml")
        parsed = yaml.safe_load(yaml_out)
        if not isinstance(parsed, dict):
            raise RuntimeError("replan YAML must be a mapping")
        tid = str(payload.get("id") or parsed.get("id") or "").strip()
        return {
            "id": tid,
            "yaml": yaml_out,
            "install_script": str(payload.get("install_script") or "").strip(),
            "notes": str(payload.get("notes") or "").strip(),
            "parsed": parsed,
        }

    def lint_skill(
        self, *, name: str, skill_md: str, files: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Lint editable skill content (no LLM)."""
        import tempfile

        nm = (name or "").strip().lower()
        md = (skill_md or "").strip()
        file_map = {str(k): str(v) for k, v in (files or {}).items()}
        if not nm or not _NAME_RE.match(nm):
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
            return SkillLinter.shared().check(skill_dir)
