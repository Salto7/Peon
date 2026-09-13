"""Resolve missing CLI installs: tools/catalog YAML → skill docs → planner."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

from orchestrator.utils.service import SharedService
from orchestrator.utils.llm import chat_json, llm_config
from orchestrator.tools.catalog.catalog import AptInstallStep, InstallStep
from orchestrator.tools.catalog import CatalogProvisioner, ToolCatalog
from orchestrator.skills.misc.registry import SkillRegistry
from orchestrator.sandbox import SandboxSession

logger = logging.getLogger(__name__)

_INSTALL_FENCE = re.compile(
    r"```(?:ya?ml)?\s*\n((?:install:|verify:)[\s\S]*?)```", re.IGNORECASE
)
_APT = re.compile(
    r"apt(?:-get)?\s+install\s+(?:-[yY]\s+)*(?:--no-install-recommends\s+)*([^\n`]+)",
    re.IGNORECASE,
)
_PIP = re.compile(r"pip(?:3)?\s+install\s+([^\n`]+)", re.IGNORECASE)
_GH = re.compile(
    r"(?:github(?:_release)?|install(?:_github)?_release)\s*[:(]?\s*"
    r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)


class InstallResolver(SharedService):
    """Cascade install instructions until the binary is on PATH."""

    def resolve(
        self,
        binary: str,
        *,
        package: str = "",
        skill_name: str = "",
    ) -> tuple[bool, str]:
        name = (binary or "").strip()
        if not name:
            return True, "no binary to provision"


        if SandboxSession.current().which(name):
            return True, f"{name} already installed"

        errors: list[str] = []

        # 1) tools/catalog YAML
        cat = self._from_catalog(name)
        if cat is not None:
            if cat[0]:
                return cat
            errors.append(cat[1])

        # Explicit package from caller (skill script) — only after catalog miss/fail.
        pkg = (package or "").strip()
        if pkg:
            ok, msg = self._apt([pkg], binary=name)
            if ok:
                return True, msg
            errors.append(msg)

        # 2) skill SKILL.md / references
        skill = (skill_name or os.environ.get("ORCHESTRATOR_SKILL_NAME") or "").strip()
        sk = self._from_skill(name, skill)
        if sk is not None:
            if sk[0]:
                return sk
            errors.append(sk[1])

        # 3) planner (LLM) — last resort
        plan = self._from_planner(
            name, skill=skill, prior_error="; ".join(errors)
        )
        if plan is not None:
            return plan

        hint = "add tools/catalog YAML, skill Install section, or set an LLM key for planner"
        detail = "; ".join(errors) or f"no install recipe for {name!r}"
        return False, f"{detail}; {hint}"

    @staticmethod
    def _from_catalog(binary: str) -> tuple[bool, str] | None:
        try:

            if ToolCatalog.shared().by_binary(binary) is None:
                return None
            return CatalogProvisioner.shared().provision_binary(binary)
        except Exception as exc:
            logger.debug("catalog resolve failed for %s: %s", binary, exc)
            return None

    def _from_skill(self, binary: str, skill_name: str) -> tuple[bool, str] | None:
        steps = self._skill_install_steps(binary, skill_name)
        if not steps:
            return None
        label = skill_name or "docs"
        return self._apply_steps(steps, binary=binary, source=f"skill:{label}")

    def _from_planner(
        self, binary: str, *, skill: str, prior_error: str
    ) -> tuple[bool, str] | None:
        try:

            if not llm_config().api_key:
                return None
            payload = chat_json(
                "You install missing CLI tools in an Ubuntu/Debian sandbox. "
                "Reply with JSON only: "
                '{"install":[{"type":"custom"|"apt"|"github_release"|"pip"|"git_clone",...}],'
                '"verify":[{"command":"..."}]}. '
                "Install priority (try in order, stop when verify passes): "
                "1) type=custom (inline shell or {binary}.sh beside the catalog YAML), "
                "2) type=apt, "
                "3) type=github_release, "
                "then pip / git_clone. "
                "No prose.",
                f"binary={binary!r} skill={skill!r} prior_error={prior_error!r}",
            )
        except Exception as exc:
            logger.info("planner install resolve failed for %s: %s", binary, exc)
            return None
        if not isinstance(payload, dict):
            return None
        steps = list(payload.get("install") or [])
        if not steps:
            return None
        return self._apply_steps(steps, binary=binary, source="planner")

    def _skill_install_steps(self, binary: str, skill_name: str) -> list[dict[str, Any]]:
        texts = self._skill_texts(binary, skill_name)
        if not texts:
            return []
        steps: list[dict[str, Any]] = []
        for text in texts:
            steps.extend(self._parse_install_docs(text, binary=binary))
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for step in steps:
            key = repr(sorted(step.items()))
            if key not in seen:
                seen.add(key)
                out.append(step)
        return out

    @staticmethod
    def _skill_texts(binary: str, skill_name: str) -> list[str]:

        reg = SkillRegistry.shared()
        skills = []
        seen: set[str] = set()
        if skill_name:
            skill = reg.load_skill(skill_name)
            if skill:
                skills.append(skill)
                seen.add(skill.name)
        needle = (binary or "").strip().lower()
        if needle:
            for skill in reg.get_registry().values():
                if skill.name in seen:
                    continue
                toolkit = {str(t).strip().lower() for t in (skill.toolkit or [])}
                if needle in toolkit:
                    skills.append(skill)
                    seen.add(skill.name)
        texts: list[str] = []
        for skill in skills:
            texts.append(skill.instructions or "")
            root = Path(skill.skill_dir)
            for path in sorted(root.glob("references/*")):
                if path.suffix.lower() in {".md", ".txt", ".yml", ".yaml"}:
                    try:
                        texts.append(path.read_text(encoding="utf-8"))
                    except OSError:
                        continue
        return texts

    def _parse_install_docs(self, text: str, *, binary: str) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = []
        for match in _INSTALL_FENCE.finditer(text or ""):
            try:
                raw = yaml.safe_load(match.group(1))
            except yaml.YAMLError:
                continue
            if isinstance(raw, dict) and isinstance(raw.get("install"), list):
                steps.extend(s for s in raw["install"] if isinstance(s, dict))
        for match in _APT.finditer(text or ""):
            pkgs = [
                p.strip().strip("'\"")
                for p in match.group(1).split()
                if p.strip() and not p.startswith("-")
            ]
            if pkgs:
                steps.append({"type": "apt", "packages": pkgs})
        for match in _PIP.finditer(text or ""):
            pkgs = [
                p.strip().strip("'\"")
                for p in match.group(1).split()
                if p.strip() and not p.startswith("-")
            ]
            if pkgs:
                steps.append({"type": "pip", "packages": pkgs})
        for match in _GH.finditer(text or ""):
            repo = match.group(1).strip()
            if repo:
                steps.append(
                    {
                        "type": "github_release",
                        "repo": repo,
                        "binary": binary,
                        "asset_substr": "linux_amd64",
                    }
                )
        return steps

    def _apply_steps(
        self, steps: list[dict[str, Any]], *, binary: str, source: str
    ) -> tuple[bool, str]:

        applied: list[str] = []
        for raw in steps:
            step = InstallStep.from_dict(raw)
            if step is None:
                continue
            ok, msg = step.apply(binary=binary, tool_id=binary)
            if not ok:
                return False, f"{source}: {msg}"
            if msg:
                applied.append(msg)
        if SandboxSession.current().which(binary):
            return True, f"{source}: " + (", ".join(applied) or f"installed {binary}")
        return False, f"{source}: applied steps but {binary!r} still missing from PATH"

    @staticmethod
    def _apt(packages: list[str], *, binary: str) -> tuple[bool, str]:

        ok, msg = AptInstallStep({"packages": packages}).apply(binary=binary)
        if not ok:
            return False, msg
        if SandboxSession.current().which(binary):
            return True, msg
        return False, f"installed {packages} but {binary!r} still not on PATH"
