"""Learn authoring driven by a ROLE.yaml pack (default: code-writer).

Peon only resolves the authoring role + runs its declared engine. Prompts,
bootstrap, and task wiring live on the role so swapping engines is a pack change.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from orchestrator.crew.roles.model import RoleSpec
from orchestrator.crew.roles.registry import RoleRegistry
from orchestrator.learn.draft import (
    authoring_prompt,
    catalog_summaries,
    filter_catalog,
    propose_missing_tool,
    tool_replan_human,
    tool_suggest_human,
    tool_suggestion_from_payload,
)
from orchestrator.learn.role_draft import assemble_role_result
from orchestrator.utils.service import SharedServiceBase
from peon.projects.llm_proxy import LlmProxy
from peon.projects.opencode_engine import OpenCodeLabEngine

PROXY_REQUIRED_MSG = (
    "LiteLLM proxy is required to create new tools/roles with the Learn "
    "authoring role. Enable LLM_PROXY_ENABLED in Settings, then retry."
)

_RESULT_HINTS = {
    "tool": (
        "Write result.json with keys: id, yaml, install_script, notes. "
        "yaml must be a full catalog tool YAML string."
    ),
    "role": (
        "Author the role pack under ./out/ (ROLE.yaml + KNOWLEDGE.md, "
        "optional references/). Also write result.json with keys: "
        "name, role_yaml, files, notes, suggested_tools. Prefer real "
        "files under ./out/ — result.json must still include full contents."
    ),
    "tool_replan": (
        "Write result.json with keys: id, yaml, install_script, notes."
    ),
}


class RoleAuthoring(SharedServiceBase):
    """Toolsmith entrypoint: bind Learn tasks to the configured authoring role."""

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

    def authoring_role(self) -> RoleSpec:
        """Resolve LEARN_AUTHORING_ROLE (default code-writer) from the catalog."""
        rid = str(
            getattr(settings, "LEARN_AUTHORING_ROLE", None) or "code-writer"
        ).strip() or "code-writer"
        reg = RoleRegistry.shared()
        role = reg.get(rid)
        if role is None:
            for candidate in reg.list_roles():
                if candidate.authoring_engine:
                    role = candidate
                    break
        if role is None:
            raise RuntimeError(
                f"Learn authoring role {rid!r} not found under ROLES_DIR; "
                "add roles/code-writer or set LEARN_AUTHORING_ROLE."
            )
        return role

    def suggest_tool(self, prompt: str) -> dict[str, Any]:
        text = (prompt or "").strip()
        if not text:
            raise ValueError("prompt is required")
        self.require_proxy()
        role = self.authoring_role()
        return tool_suggestion_from_payload(
            self._run_role_task(role, task="tool", human=tool_suggest_human(text)),
            author=role.authoring_engine or role.id,
        )

    def write_role(
        self, prompt: str, *, tools: list[str] | None = None
    ) -> dict[str, Any]:
        text = (prompt or "").strip()
        if not text:
            raise ValueError("prompt is required")
        self.require_proxy()
        role = self.authoring_role()
        tool_suggestion, proposed, install_recipes = propose_missing_tool(
            text, self.suggest_tool
        )
        payload = self._run_role_task(
            role,
            task="role",
            human=authoring_prompt(
                text,
                filter_catalog(catalog_summaries(), tools),
                proposed=proposed,
                tool_recipes=install_recipes or None,
            ),
        )
        return assemble_role_result(
            payload,
            prompt=text,
            author=role.authoring_engine or role.id,
            tool_suggestion=tool_suggestion,
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
        role = self.authoring_role()
        return tool_suggestion_from_payload(
            self._run_role_task(
                role,
                task="tool_replan",
                human=tool_replan_human(
                    prompt=prompt,
                    yaml_text=yaml_text,
                    install_script=install_script,
                    error=error,
                    feedback=feedback,
                ),
            ),
            author=role.authoring_engine or role.id,
        )

    def _run_role_task(
        self, role: RoleSpec, *, task: str, human: str
    ) -> dict[str, Any]:
        system = role.authoring_prompt_text(task)
        knowledge = role.knowledge_text()
        if knowledge:
            system = f"{system.rstrip()}\n\n## Role knowledge\n{knowledge}".strip()
        engine = (role.authoring_engine or "opencode").strip().lower()
        if engine != "opencode":
            raise RuntimeError(
                f"role {role.id!r} authoring.engine={engine!r} is not supported; "
                "Peon currently ships an OpenCode lab engine. Change the role pack "
                "or extend RoleAuthoring engines."
            )
        return OpenCodeLabEngine().run_json_task(
            kind=f"{role.id}-{task}",
            system=system,
            human=human,
            result_hint=_RESULT_HINTS.get(
                task, "Write result.json with the authored artifact."
            ),
            bootstrap_script=role.authoring_bootstrap_text(),
        )


# Back-compat alias for imports that still say OpenCodeAuthoring.
OpenCodeAuthoring = RoleAuthoring
