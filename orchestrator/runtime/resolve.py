"""Resolve missing CLI installs: tools/catalog YAML → skill docs → planner."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from orchestrator.utils.service import SharedService
from orchestrator.utils.llm import chat_json, llm_config
from orchestrator.utils.install_docs import install_steps_from_fences
from orchestrator.prompts import INSTALL_CASCADE, PLANNER_INSTALL_SYSTEM
from orchestrator.tools.catalog.catalog import AptInstallStep, InstallStep
from orchestrator.tools.catalog import CatalogProvisioner, ToolCatalog
from orchestrator.skills.misc.registry import SkillRegistry
from orchestrator.sandbox import SandboxSession

logger = logging.getLogger(__name__)


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

        hint = (
            f"add tools/catalog YAML, skill INSTALL.md, or set an LLM key "
            f"({INSTALL_CASCADE})"
        )
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
                PLANNER_INSTALL_SYSTEM,
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
            steps.extend(self._parse_install_docs(text))
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

    @staticmethod
    def _parse_install_docs(text: str) -> list[dict[str, Any]]:
        return install_steps_from_fences(text)

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
