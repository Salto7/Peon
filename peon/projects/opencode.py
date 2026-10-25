"""OpenCode — the other ``CodeWritingEngine``, inside the Toolsmith lab only.

See ``orchestrator.toolsmith.engine.CodeWritingEngine`` for the shared
contract and every prompt both engines use; see ``orchestrator.toolsmith
.peon_engine.PeonAuthoring`` for the plain-``chat_json`` one. OpenCode's own
job is narrower than "authoring": it writes and runs the actual provisioning
code a tool or a skill's script needs, inside a real sandbox, so the draft
is something that has actually executed — not just plausible-looking
YAML/shell text.

Scope fence, deliberate and not just incidental: this module is reached only
from Toolsmith (``peon/projects/toolsmith.py``). Nothing here runs against
an engagement project or its sandbox, and nothing in ``job_run.py`` or any
``orchestrator/crew/runtimes/*`` may import it — if a future change wants
OpenCode driving real project work, that is a different, much bigger
decision than this module makes today.

OpenCode itself is baked into the lab image (``Docker/sandbox-lab/``) at a
pinned version — never fetched here at authoring time. See ``Lab`` for the
one-container lifecycle both this module and install-testing share.
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

from orchestrator.toolsmith.draft import finalize_skill_payload, skill_result_dict, valid_skill_name
from orchestrator.toolsmith.engine import CodeWritingEngine
from orchestrator.utils.service import SharedServiceBase
from peon.projects.lab_runtime import Lab
from peon.projects.llm_proxy import LlmProxy

logger = logging.getLogger(__name__)

PROXY_REQUIRED_MSG = (
    "LiteLLM proxy is required to create new skills/tools with OpenCode. "
    "Enable LLM_PROXY_ENABLED in Settings, then retry."
)

# One generic harness instruction for every kind: the skill-specific ./out/
# guidance is harmless noise for a tool/replan task (nothing to put there),
# so one template beats three near-identical ones.
_AGENTS_MD = """# Peon Toolsmith authoring task

Read SYSTEM.md and REQUEST.md, then do what REQUEST.md asks.

If the task is a skill: author it under ./out/ as real files (SKILL.md, a
shebang script when a CLI is required, references/ when linked) — not just
described in result.json.

Write result.json at the workspace root when done, with the keys REQUEST.md
asked for. For a skill, result.json's own content can be a summary; the
files under ./out/ are what's kept. Do not ask questions. Use only relative
paths under this workspace.
"""


class OpenCodeEngine(CodeWritingEngine, SharedServiceBase):
    """``CodeWritingEngine`` that runs OpenCode in the Toolsmith lab."""

    engine_id = "opencode"

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

    def _run(self, *, kind: str, system: str, human: str) -> dict[str, Any]:
        self.require_proxy()
        lab = Lab.shared()
        staging = lab.staging_root()
        draft = Path(tempfile.mkdtemp(prefix=f"opencode-{kind}-", dir=str(staging)))
        rel = draft.relative_to(staging).as_posix()
        try:
            (draft / "SYSTEM.md").write_text(system.rstrip() + "\n", encoding="utf-8")
            (draft / "REQUEST.md").write_text(human.rstrip() + "\n", encoding="utf-8")
            (draft / "out").mkdir(parents=True, exist_ok=True)
            (draft / "AGENTS.md").write_text(_AGENTS_MD, encoding="utf-8")
            self._write_opencode_config(draft)

            # Always a freshly-reset lab: OpenCode is baked into the image
            # (no install step needed), so there is no reuse-for-speed
            # tradeoff left to make — every authoring task gets a known-clean
            # container, the same guarantee an install test gets.
            lab._ensure_clean()
            session = lab.connect()

            model = (
                str(getattr(settings, "OPENCODE_MODEL", None) or "peon/default").strip()
                or "peon/default"
            )
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
                    f"OpenCode finished without result.json (stdout: {(run.stdout or '')[:400]})"
                )
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"OpenCode result.json is invalid: {exc}") from exc
            if not isinstance(payload, dict):
                raise RuntimeError("OpenCode result.json must be an object")
            # Stash the ./out/ files for _finish_skill — a tool/replan kind
            # never writes any, so this is a no-op for those.
            payload["_out_files"] = self._collect_files(draft / "out")
            payload["_out_dir"] = str(draft / "out")
            logger.info(
                "opencode authoring ok kind=%s lab=%s draft=%s",
                kind,
                lab.container_name(),
                draft,
            )
            return payload
        finally:
            shutil.rmtree(draft, ignore_errors=True)

    def _finish_skill(self, payload: dict[str, Any], **context: Any) -> dict[str, Any]:
        out_files = payload.pop("_out_files", {}) or {}
        out_dir = Path(payload.pop("_out_dir", ""))
        if out_files:
            files = dict(payload.get("files") or {})
            files.update(out_files)
            if "SKILL.md" in out_files and not payload.get("skill_md"):
                payload["skill_md"] = out_files["SKILL.md"]
            payload["files"] = {k: v for k, v in files.items() if k != "SKILL.md"}
            if not payload.get("name") and out_dir.is_dir():
                for child in out_dir.iterdir():
                    if child.is_dir() and valid_skill_name(child.name):
                        payload["name"] = child.name
                        break

        name, skill_md, files, suggested, mode, lint = finalize_skill_payload(
            payload,
            prompt=context["prompt"],
            proposed=context["proposed"],
            install_recipes=context["install_recipes"],
        )
        return skill_result_dict(
            name=name,
            skill_md=skill_md,
            files=files,
            notes=str(payload.get("notes") or "").strip(),
            suggested=suggested,
            mode=mode,
            lint=lint,
            tool_suggestion=context["tool_suggestion"],
            author=self.engine_id,
        )

    def _write_opencode_config(self, draft: Path) -> None:
        proxy = LlmProxy.shared()
        model_id = "default"
        litellm_model = str(
            getattr(settings, "LITELLM_MODEL", None) or "openrouter/openai/gpt-5.6-luna"
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
        (draft / "opencode.json").write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _collect_files(root: Path) -> dict[str, str]:
        if not root.is_dir():
            return {}
        skill_roots = [p for p in root.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()]
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
                "_out_files": {},
                "_out_dir": "",
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
