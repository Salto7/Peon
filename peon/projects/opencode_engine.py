"""Thin Learn-lab OpenCode runner (no role/prompt knowledge)."""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings

from peon.projects.learn_lab import LearnLab
from peon.projects.llm_proxy import LlmProxy

logger = logging.getLogger(__name__)


class OpenCodeLabEngine:
    """Run ``opencode`` in the Learn lab against the LiteLLM proxy."""

    def run_json_task(
        self,
        *,
        kind: str,
        system: str,
        human: str,
        result_hint: str,
        bootstrap_script: str = "",
    ) -> dict[str, Any]:
        lab = LearnLab.shared()
        staging = lab.staging_root()
        draft = Path(tempfile.mkdtemp(prefix=f"opencode-{kind}-", dir=str(staging)))
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
            self._write_config(draft)

            info = lab.ensure_for_authoring(workspace_host=str(staging))
            session = lab.connect()
            bootstrap = (bootstrap_script or "").strip() or (
                "command -v opencode || "
                '{ curl -fsSL https://opencode.ai/install | bash; '
                'export PATH="$HOME/.opencode/bin:$PATH"; command -v opencode; }'
            )
            boot = session.exec(bootstrap, timeout=300, shell=True)
            if boot.code != 0:
                detail = (boot.stderr or boot.stdout or "").strip()[:500]
                raise RuntimeError(f"authoring engine bootstrap failed: {detail}")

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

            payload = self._load_result(draft, run.stdout or "")
            out_files = self._collect_files(draft / "out")
            if out_files:
                files = dict(payload.get("files") or {})
                files.update(out_files)
                # Prefer ROLE.yaml / tool yaml from out/
                if "ROLE.yaml" in out_files and not payload.get("role_yaml"):
                    payload["role_yaml"] = out_files["ROLE.yaml"]
                payload["files"] = {
                    k: v
                    for k, v in files.items()
                    if k not in {"ROLE.yaml", "ROLE.yml"}
                }
            logger.info(
                "opencode engine ok kind=%s lab=%s draft=%s", kind, info.name, draft
            )
            return payload
        finally:
            shutil.rmtree(draft, ignore_errors=True)

    def _write_config(self, draft: Path) -> None:
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

    def _load_result(self, draft: Path, stdout: str) -> dict[str, Any]:
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
                f"OpenCode finished without result.json (stdout: {stdout[:400]})"
            )
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OpenCode result.json is invalid: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("OpenCode result.json must be an object")
        return payload

    @staticmethod
    def _collect_files(root: Path) -> dict[str, str]:
        if not root.is_dir():
            return {}
        role_roots = [
            p for p in root.iterdir() if p.is_dir() and (p / "ROLE.yaml").is_file()
        ]
        base = role_roots[0] if role_roots else root
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
        role_yaml = files.pop("ROLE.yaml", "") or files.pop("ROLE.yml", "")
        if role_yaml:
            name = ""
            for child in out_dir.iterdir():
                if child.is_dir() and (child / "ROLE.yaml").is_file():
                    name = child.name
                    break
            return {
                "name": name or "authored-role",
                "role_yaml": role_yaml,
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
