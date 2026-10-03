"""OpenCode authoring inside the Learn lab (via LiteLLM proxy).

Replaces thin ``chat_json`` scaffolding for Toolsmith create flows with a real
coding agent that edits files on disk in the lab workspace.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings

from orchestrator.learn.draft import (
    authoring_prompt,
    catalog_summaries,
    filter_catalog,
    finalize_skill_payload,
    propose_missing_tool,
    skill_prompt,
    skill_result_dict,
    tool_replan_human,
    tool_suggest_human,
    tool_suggestion_from_payload,
    valid_skill_name,
)
from orchestrator.utils.service import SharedServiceBase
from peon.projects.learn_lab import LearnLab
from peon.projects.llm_proxy import LlmProxy

logger = logging.getLogger(__name__)

PROXY_REQUIRED_MSG = (
    "LiteLLM proxy is required to create new skills/tools with OpenCode. "
    "Enable LLM_PROXY_ENABLED in Settings, then retry."
)

_OPENCODE_INSTALL = (
    "export DEBIAN_FRONTEND=noninteractive; "
    "apt-get update -qq && "
    "apt-get install -y --no-install-recommends ca-certificates curl bash "
    ">/dev/null && "
    "if ! command -v opencode >/dev/null 2>&1; then "
    "  curl -fsSL https://opencode.ai/install | bash; "
    "  export PATH=\"$HOME/.opencode/bin:$PATH\"; "
    "fi; "
    "command -v opencode"
)

_TOOLS_SUGGESTOR = "tools-suggestor"
_SKILL_WRITER = "skill-writer"


class OpenCodeAuthoring(SharedServiceBase):
    """Run OpenCode in the Learn lab against Peon's LiteLLM /v1 proxy."""

    def require_proxy(self) -> None:
        if not LlmProxy.intent_enabled():
            raise RuntimeError(PROXY_REQUIRED_MSG)
        proxy = LlmProxy.shared()
        st = proxy.status()
        if not st.get("running"):
            result = proxy.ensure()
            if not result.get("ok"):
                raise RuntimeError(
                    "LiteLLM proxy is enabled but not running: "
                    f"{result.get('error') or 'start failed'}. "
                    "Check Settings → LLM proxy, or docker logs for peon-litellm."
                )

    def suggest_tool(self, prompt: str) -> dict[str, Any]:
        text = (prompt or "").strip()
        if not text:
            raise ValueError("prompt is required")
        self.require_proxy()
        payload = self._run_json_task(
            kind="tool",
            system=skill_prompt(_TOOLS_SUGGESTOR),
            human=tool_suggest_human(text),
            result_hint=(
                "Write result.json with keys: id, yaml, install_script, notes. "
                "yaml must be a full catalog tool YAML string."
            ),
        )
        return tool_suggestion_from_payload(payload, author="opencode")

    def write_skill(
        self, prompt: str, *, tools: list[str] | None = None
    ) -> dict[str, Any]:
        text = (prompt or "").strip()
        if not text:
            raise ValueError("prompt is required")
        self.require_proxy()
        catalog = filter_catalog(catalog_summaries(), tools)

        tool_suggestion, proposed, install_recipes = propose_missing_tool(
            text, self.suggest_tool
        )

        payload = self._run_json_task(
            kind="skill",
            system=skill_prompt(_SKILL_WRITER),
            human=authoring_prompt(
                text,
                catalog,
                proposed=proposed,
                tool_recipes=install_recipes or None,
            ),
            result_hint=(
                "Author the skill package under ./out/ (SKILL.md + scripts/, "
                "references/ as needed). Also write result.json with keys: "
                "name, skill_md, files (object of relative path → content), "
                "notes, suggested_tools. Prefer real files under ./out/ — "
                "result.json must still include the full contents."
            ),
        )
        if not isinstance(payload, dict):
            raise RuntimeError("OpenCode returned non-object JSON for skill")

        name, skill_md, files, suggested, mode, lint = finalize_skill_payload(
            payload,
            prompt=text,
            proposed=proposed,
            install_recipes=install_recipes,
        )
        return skill_result_dict(
            name=name,
            skill_md=skill_md,
            files=files,
            notes=str(payload.get("notes") or "").strip(),
            suggested=suggested,
            mode=mode,
            lint=lint,
            tool_suggestion=tool_suggestion,
            author="opencode",
        )

    def replan_tool(
        self,
        *,
        prompt: str,
        yaml_text: str,
        install_script: str = "",
        error: str = "",
        feedback: str = "",
    ) -> dict[str, Any]:
        self.require_proxy()
        payload = self._run_json_task(
            kind="tool-replan",
            system=skill_prompt(_TOOLS_SUGGESTOR),
            human=tool_replan_human(
                prompt=prompt,
                yaml_text=yaml_text,
                install_script=install_script,
                error=error,
                feedback=feedback,
            ),
            result_hint=(
                "Write result.json with keys: id, yaml, install_script, notes."
            ),
        )
        return tool_suggestion_from_payload(payload, author="opencode")

    def _run_json_task(
        self,
        *,
        kind: str,
        system: str,
        human: str,
        result_hint: str,
    ) -> dict[str, Any]:
        lab = LearnLab.shared()
        staging = lab.staging_root()
        draft = Path(
            tempfile.mkdtemp(prefix=f"opencode-{kind}-", dir=str(staging))
        )
        rel = draft.relative_to(staging).as_posix()
        try:
            (draft / "SYSTEM.md").write_text(system.rstrip() + "\n", encoding="utf-8")
            (draft / "REQUEST.md").write_text(human.rstrip() + "\n", encoding="utf-8")
            (draft / "out").mkdir(parents=True, exist_ok=True)
            (draft / "AGENTS.md").write_text(
                (
                    "# Peon Toolsmith authoring task\n\n"
                    "Read SYSTEM.md and REQUEST.md.\n"
                    f"{result_hint}\n"
                    "Do not ask questions. Write result.json at the workspace root.\n"
                    "Use only relative paths under this workspace.\n"
                ),
                encoding="utf-8",
            )
            self._write_opencode_config(draft)

            info = lab.ensure_for_authoring(workspace_host=str(staging))
            session = lab.connect()
            bootstrap = session.exec(_OPENCODE_INSTALL, timeout=300, shell=True)
            if bootstrap.code != 0:
                detail = (bootstrap.stderr or bootstrap.stdout or "").strip()[:500]
                raise RuntimeError(f"OpenCode install failed in Learn lab: {detail}")

            model = str(
                getattr(settings, "OPENCODE_MODEL", None) or "peon/default"
            ).strip() or "peon/default"
            message = (
                "Complete the Peon Toolsmith authoring task in AGENTS.md. "
                "Write result.json when done."
            )
            workdir = f"/workspace/{rel}"
            cmd = (
                'export PATH="$HOME/.opencode/bin:/usr/local/bin:$PATH"; '
                f"cd {json.dumps(workdir)} && "
                f"opencode run --auto --model {json.dumps(model)} "
                f"{json.dumps(message)}"
            )
            run = session.exec(cmd, timeout=900, shell=True)
            if run.code != 0:
                detail = (run.stderr or run.stdout or "").strip()[:800]
                raise RuntimeError(f"OpenCode authoring failed: {detail}")

            result_path = draft / "result.json"
            if not result_path.is_file():
                alt = draft / "out" / "result.json"
                if alt.is_file():
                    result_path = alt
            if not result_path.is_file():
                recovered = self._recover_from_out(draft / "out")
                if recovered is not None:
                    return recovered
                raise RuntimeError(
                    "OpenCode finished without result.json "
                    f"(stdout: {(run.stdout or '')[:400]})"
                )
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"OpenCode result.json is invalid: {exc}") from exc
            if not isinstance(payload, dict):
                raise RuntimeError("OpenCode result.json must be an object")
            out_files = self._collect_files(draft / "out")
            if out_files and kind.startswith("skill"):
                files = dict(payload.get("files") or {})
                files.update(out_files)
                if "SKILL.md" in out_files and not payload.get("skill_md"):
                    payload["skill_md"] = out_files["SKILL.md"]
                payload["files"] = {
                    k: v for k, v in files.items() if k != "SKILL.md"
                }
                if not payload.get("name"):
                    for child in (draft / "out").iterdir():
                        if child.is_dir() and valid_skill_name(child.name):
                            payload["name"] = child.name
                            break
            logger.info(
                "opencode authoring ok kind=%s lab=%s draft=%s",
                kind,
                info.name,
                draft,
            )
            return payload
        finally:
            shutil.rmtree(draft, ignore_errors=True)

    def _write_opencode_config(self, draft: Path) -> None:
        proxy = LlmProxy.shared()
        model_id = "default"
        litellm_model = str(
            getattr(settings, "LITELLM_MODEL", None) or "openrouter/openai/gpt-4o-mini"
        )
        cfg = {
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                "peon": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Peon LiteLLM",
                    "options": {
                        "baseURL": proxy.proxy_url(),
                        "apiKey": proxy.master_key(),
                    },
                    "models": {
                        model_id: {"name": "default"},
                        litellm_model: {"name": litellm_model},
                    },
                }
            },
            "model": f"peon/{model_id}",
            "enabled_providers": ["peon"],
        }
        (draft / "opencode.json").write_text(
            json.dumps(cfg, indent=2) + "\n", encoding="utf-8"
        )

    @staticmethod
    def _collect_files(root: Path) -> dict[str, str]:
        if not root.is_dir():
            return {}
        skill_roots = [
            p for p in root.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()
        ]
        base = skill_roots[0] if skill_roots else root
        out: dict[str, str] = {}
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(base).as_posix()
            if rel.startswith(".") or "/." in rel:
                continue
            try:
                out[rel] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
        return out

    def _recover_from_out(self, out_dir: Path) -> dict[str, Any] | None:
        files = self._collect_files(out_dir)
        if not files:
            return None
        skill_md = files.pop("SKILL.md", "")
        if skill_md:
            name = ""
            for child in out_dir.iterdir():
                if child.is_dir() and (child / "SKILL.md").is_file():
                    name = child.name
                    break
            return {
                "name": name or "authored-skill",
                "skill_md": skill_md,
                "files": files,
                "notes": "Recovered from OpenCode ./out (no result.json).",
                "suggested_tools": [],
            }
        for key in ("tool.yaml", "catalog.yaml", "result.yaml"):
            if key in files:
                try:
                    parsed = yaml.safe_load(files[key])
                except yaml.YAMLError:
                    parsed = None
                tid = ""
                if isinstance(parsed, dict):
                    tid = str(parsed.get("id") or "").strip()
                return {
                    "id": tid,
                    "yaml": files[key],
                    "install_script": files.get("install.sh")
                    or (files.get(f"{tid}.sh", "") if tid else ""),
                    "notes": files.get("notes.md", ""),
                }
        return None
