"""Pick CrewAI roles for a brief via catalog tags + LLM (no regex maps)."""

from __future__ import annotations

import json

from orchestrator.crew.roles.hierarchy import engagement_bookends, manager_role
from orchestrator.crew.roles.registry import RoleRegistry
from orchestrator.utils.llm import chat_text, llm_configured
from orchestrator.utils.service import SharedServiceBase
from orchestrator.utils.strings import extract_json


class RoleRouter(SharedServiceBase):
    """Resolve roles from the live roles/ catalog using tags + AI."""

    SYSTEM = """You pick CrewAI roles for Peon from the catalog below.
Rules:
- Only use role ids that appear in the catalog.
- Prefer the smallest useful set.
- Match the brief to each role's goal AND capabilities/tags.
- Use hierarchy: roles with reports_to=<manager> are specialists the manager hires.
- Engagement briefs: include the manager role (allow_delegation, no reports_to) when
  multi-step work is needed; include the analyzer (tags include report) when a
  full project report is expected.
- Authoring / Learn / draft briefs (mode=authoring): pick authoring roles only —
  do not hire network/probe specialists.
Reply ONLY JSON: {"roles":["id",...],"reason":"short"}"""

    def resolve(
        self,
        description: str,
        *,
        explicit: list[str] | None = None,
        project: bool = True,
    ) -> list[str]:
        catalog = {r.id for r in RoleRegistry.shared().list_roles()}
        out: list[str] = []
        for raw in explicit or []:
            rid = str(raw or "").strip()
            if rid in catalog and rid not in out:
                out.append(rid)
        if out:
            return out

        text = (description or "").strip()
        mgr_id, ana_id = engagement_bookends()

        if not text:
            return [mgr_id] if project and mgr_id else []

        if llm_configured() and catalog:
            try:
                raw = chat_text(
                    self.SYSTEM,
                    f"Brief:\n{text[:3000]}\n\nproject={project}\n\n"
                    f"Roles:\n{self._catalog_prompt_lines()}",
                )
                data = json.loads(extract_json(raw))
                picked = data.get("roles") if isinstance(data, dict) else []
                for item in picked or []:
                    rid = str(item or "").strip()
                    if rid in catalog and rid not in out:
                        out.append(rid)
            except Exception:
                out = []

        if out:
            return out

        # Offline / LLM failure: manager only for projects; empty for jobs.
        if project and mgr_id:
            return [mgr_id]
        return []

    def _catalog_prompt_lines(self) -> str:
        lines: list[str] = []
        for r in RoleRegistry.shared().list_roles():
            caps = ", ".join(r.capabilities) if r.capabilities else "-"
            reports = r.reports_to or "(none)"
            flags: list[str] = [r.mode or "engagement"]
            if r.is_manager:
                flags.append("manager")
            if r.is_analyzer:
                flags.append("analyzer")
            if r.requires_roe:
                flags.append("requires_roe")
            if r.assets:
                flags.append(f"assets={len(r.assets)}")
            lines.append(
                f"- {r.id} [{'/'.join(flags)}] tags=[{caps}] "
                f"reports_to={reports}: {r.goal[:160]}"
            )
        return "\n".join(lines)

    def roles_index(self) -> list[dict]:
        return [
            {
                "id": r.id,
                "label": r.label,
                "goal": r.goal,
                "capabilities": list(r.capabilities),
                "reports_to": r.reports_to,
                "tools": list(r.tools),
                "mode": r.mode,
                "authoring": r.is_authoring,
                "is_manager": r.is_manager,
                "is_analyzer": r.is_analyzer,
                "assets": list(r.assets),
            }
            for r in RoleRegistry.shared().list_roles()
        ]

    def roles_index_text(self) -> str:
        """Compact catalog text for planner prompts."""
        mgr = manager_role()
        lines = [
            "Available CrewAI roles (assign via role_id on each objective).",
            "Match role_id using goal text AND capabilities/tags.",
            "Respect hierarchy.reports_to when sequencing work.",
        ]
        if mgr:
            lines.append(f"Engagement manager id: `{mgr.id}` (hire specialists under it).")
        for r in RoleRegistry.shared().list_roles():
            caps = ", ".join(r.capabilities) if r.capabilities else "-"
            reports = r.reports_to or "(none)"
            lines.append(
                f"- `{r.id}` [{r.mode}] — {r.label}: {r.goal[:180]} "
                f"tags=[{caps}] reports_to={reports}"
            )
        return "\n".join(lines)
