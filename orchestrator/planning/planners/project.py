"""Project-mode planner: objectives JSON → markdown."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from orchestrator.crew.constants import engagement_end_role, engagement_start_role
from orchestrator.planning.planner_base import PlannerBase
from orchestrator.utils.strings import extract_json

PROJECT_PLAN_SYSTEM = """You are the PROJECT planner for an authorized Peon engagement (CrewAI roles).

Mission: decompose the engagement into kill-chain OBJECTIVES. You do not execute.
You produce the shared project plan (objectives + dependencies) for CrewAI roles.

## Hard constraints (RoE)
- Plan ONLY against in-scope subjects; never expand scope
- NEVER plan actions against exclusions
- If RoE / in-scope is empty: manager + passive/OSINT only; do not invent attack targets
- Roles with requires_roe / active probing need in-scope values
- Project data lives under workspace/ and findings/

## Planning principles
- First objective MUST use the engagement manager role_id from the Role catalog
  (allow_delegation, empty reports_to)
- Last objective MUST use the analyzer role_id (capabilities include report)
- Keep 3–8 objectives total including those bookends
- Middle objectives MUST use a role_id from the Role catalog (never invent names)
  Match using goal + capabilities/tags + reports_to from the Role catalog block
  Authoring/Learn (mode=authoring) only when the brief asks to draft/suggest
- role_id is the PRIMARY role for the Job that runs the objective
- depends_on uses 1-based indices in THIS objectives list
- Parallel only when independent; otherwise chain with depends_on
- No ethics lectures, no prose outside JSON

## Quality bar for each objective
- title: short, verb-led
- phase: recon | initial-access | post-exploit | reporting
- description: concrete actions on named in-scope subjects
- acceptance_criteria: observable done condition
- mitre: technique ids when known; else []
- role_id: REQUIRED catalog role id
- commands: 0–4 dry-run capability hints using only tools exposed by the chosen role

## Output
Return ONLY a JSON object:
{
  "approach": "project",
  "goal": "one-line project goal tied to in-scope subjects",
  "objectives": [
    {
      "title": "short title",
      "phase": "recon | initial-access | post-exploit | reporting",
      "description": "what to do",
      "acceptance_criteria": "observable done condition",
      "mitre": ["T1046"],
      "depends_on": [1],
      "role_id": "<catalog role id — required>",
      "commands": ["assert_in_scope(\"<target>\")", "run_cli(\"…\")"]
    }
  ]
}
"""


def _format_target_line(item) -> str:
    if isinstance(item, dict):
        typ = str(item.get("type") or "other").strip() or "other"
        value = str(item.get("value") or "").strip()
        if not value:
            return ""
        return value if typ == "other" else f"{typ}:{value}"
    return str(item).strip()


def format_roe_block(roe) -> str:
    if roe is None:
        return "RoE: (missing — do not invent targets; blueprint/passive only)"
    in_scope = ", ".join(
        line for x in (roe.in_scope or []) if (line := _format_target_line(x))
    ) or "(empty)"
    excl = ", ".join(
        line for x in (roe.exclusions or []) if (line := _format_target_line(x))
    ) or "(none)"
    parts = [
        f"In-scope (authorized values; type optional hint): {in_scope}",
        f"Exclusions: {excl}",
    ]
    seed = getattr(roe, "seed", None) or []
    if seed:
        seed_line = ", ".join(
            line for x in seed if (line := _format_target_line(x))
        )
        if seed_line:
            parts.append(f"Seed / intent (not attack scope): {seed_line}")
    if roe.authorization_note:
        parts.append(f"Authorization: {roe.authorization_note.strip()}")
    if roe.testing_window_notes:
        parts.append(f"Window: {roe.testing_window_notes.strip()}")
    if roe.abort_triggers:
        parts.append(f"Abort: {roe.abort_triggers.strip()}")
    return "RoE:\n- " + "\n- ".join(parts)


def format_roles_block(role_ids: list[str] | None) -> str:
    """Optional agent-profile / role ids for profile_suggestion."""
    ids = [str(x).strip() for x in (role_ids or []) if str(x).strip()]
    if not ids:
        return ""
    return "Agent roles (optional profile_suggestion values):\n- " + "\n- ".join(ids)


def parse_project_objectives(raw: str) -> dict[str, Any]:
    """Parse project-plan JSON; tolerate fenced or prose-wrapped output."""
    text = extract_json(raw or "")
    try:
        data = json.loads(text)
    except Exception:
        return {
            "approach": "",
            "goal": "",
            "objectives": bookend_project_objectives([]),
        }
    if not isinstance(data, dict):
        return {
            "approach": "",
            "goal": "",
            "objectives": bookend_project_objectives([]),
        }
    objs = data.get("objectives") or []
    if not isinstance(objs, list):
        objs = []
    normalized: list[dict] = []
    for o in objs:
        if not isinstance(o, dict):
            continue
        row = dict(o)
        if not row.get("profile_suggestion") and row.get("profile"):
            row["profile_suggestion"] = row.get("profile")
        cmds = row.get("commands") or []
        if isinstance(cmds, str):
            cmds = [cmds]
        row["commands"] = [str(c).strip() for c in cmds if str(c).strip()]
        normalized.append(row)
    data["objectives"] = bookend_project_objectives(normalized)
    return data


def _is_bookend_objective(obj: dict[str, Any]) -> bool:
    role = str(obj.get("role_id") or obj.get("skill_suggestion") or "").strip()
    phase = str(obj.get("phase") or "").strip().lower()
    start, end = engagement_start_role(), engagement_end_role()
    if role and role in {start, end}:
        return True
    return phase == "reporting"


def bookend_project_objectives(objectives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure every project plan starts with manager and ends with analyzer."""

    start = engagement_start_role()
    end = engagement_end_role()
    if not start or not end:
        return [dict(o) for o in (objectives or []) if isinstance(o, dict)]

    raw = [dict(o) for o in (objectives or []) if isinstance(o, dict)]
    survivors: list[tuple[int, dict[str, Any]]] = []
    for idx, obj in enumerate(raw, start=1):
        if not _is_bookend_objective(obj):
            survivors.append((idx, obj))

    manager = {
        "title": "Validate engagement plan",
        "phase": "recon",
        "description": (
            "Validate RoE, objective dependencies, and role assignments before "
            "execution. Record blockers and establish the authorized handoff order."
        ),
        "acceptance_criteria": "Plan validated with executable role handoffs under RoE",
        "mitre": [],
        "depends_on": [],
        "role_id": start,
        "commands": [],
    }
    middle: list[dict[str, Any]] = []
    old_to_final: dict[int, int] = {}
    for old_idx, obj in survivors:
        final_idx = len(middle) + 2
        deps: list[int] = [1]
        for dep in obj.get("depends_on") or []:
            try:
                dep_i = int(dep)
            except (TypeError, ValueError):
                continue
            mapped = old_to_final.get(dep_i)
            if mapped is not None and mapped not in deps:
                deps.append(mapped)
        row = dict(obj)
        row["depends_on"] = deps
        # Accept legacy planner key during transition.
        role = str(row.get("role_id") or row.get("skill_suggestion") or "").strip()
        if not role:
            continue
        row["role_id"] = role
        row.pop("skill_suggestion", None)
        old_to_final[old_idx] = final_idx
        middle.append(row)

    last_middle = 1 + len(middle)
    analyzer = {
        "title": "Project report",
        "phase": "reporting",
        "description": (
            "Synthesize a standalone findings/report.md from workspace evidence. "
            "Do not collect new evidence or invent discoveries."
        ),
        "acceptance_criteria": "findings/report.md written as the sole project report",
        "mitre": [],
        "depends_on": [last_middle],
        "role_id": end,
        "commands": [],
    }
    return [manager, *middle, analyzer]


def bookend_role_ids(names: list[str] | None) -> list[str]:
    """Put manager first and analyzer last; drop duplicates of either."""

    start, end = engagement_start_role(), engagement_end_role()
    bookends = {x for x in (start, end) if x}
    middle: list[str] = []
    seen: set[str] = set()
    for raw in names or []:
        name = str(raw or "").strip()
        if not name or name in bookends or name in seen:
            continue
        seen.add(name)
        middle.append(name)
    out: list[str] = []
    if start:
        out.append(start)
    out.extend(middle)
    if end:
        out.append(end)
    return out


def render_project_objectives(project_title: str, payload: dict[str, Any], objectives: list) -> str:
    """Render a project plan as markdown."""
    lines = [
        f"# Project plan — {project_title}",
        "",
        f"**Goal:** {(payload.get('goal') or '').strip() or '(see objectives)'}",
        f"**Approach:** {(payload.get('approach') or '').strip() or 'project'}",
        "",
        "## Objectives",
        "",
    ]
    for obj in objectives:
        mitre = ", ".join(obj.mitre_techniques or []) or "—"
        lines.append(
            f"{obj.seq}. **{obj.title}** [{obj.phase}] `{obj.status}` — {obj.description or ''}".rstrip()
        )
        lines.append(f"   - Acceptance: {obj.acceptance_criteria or '—'}")
        lines.append(f"   - MITRE: {mitre}")
        role = getattr(obj, "role_id", "") or getattr(obj, "skill_suggestion", "") or ""
        if role:
            lines.append(f"   - Role: `{role}`")
        profile = getattr(obj, "profile_suggestion", "") or ""
        if profile:
            lines.append(f"   - Profile: `{profile}`")
        cmds = list(getattr(obj, "commands", None) or [])
        if cmds:
            lines.append("   - Commands (dry-run):")
            for cmd in cmds:
                lines.append(f"     - `{cmd}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _objectives_from_payload(payload: dict[str, Any]) -> list:
    objs = []
    for i, o in enumerate(payload.get("objectives") or [], start=1):
        objs.append(
            SimpleNamespace(
                seq=i,
                title=o.get("title") or f"Objective {i}",
                phase=o.get("phase") or "",
                status="pending",
                description=o.get("description") or "",
                acceptance_criteria=o.get("acceptance_criteria") or "",
                mitre_techniques=list(o.get("mitre") or []),
                role_id=o.get("role_id") or o.get("skill_suggestion") or "",
                profile_suggestion=o.get("profile_suggestion") or o.get("profile") or "",
                commands=list(o.get("commands") or []),
            )
        )
    return objs


class ProjectPlanner(PlannerBase):
    plan_title = "Project plan"

    @property
    def system_prompt(self) -> str:
        return PROJECT_PLAN_SYSTEM

    def build_messages(
        self,
        description: str,
        *,
        project_title: str = "adhoc",
        project_summary: str = "",
        roe=None,
        prior_plan: str = "",
        memory_block: str = "",
        existing_objectives_summary: str = "",
        filtered_roles_index: str = "",
        focus_tags: list[str] | None = None,
        role_ids: list[str] | None = None,
    ) -> list:
        parts = [
            f"Project: {project_title}",
            f"Summary: {(project_summary or '').strip() or '(none)'}",
            f"Job brief:\n{(description or '').strip()}",
            format_roe_block(roe),
        ]
        if filtered_roles_index.strip():
            parts.append("Role catalog:\n" + filtered_roles_index.strip())
        if focus_tags:
            parts.append(
                "Project focus tags (preference, not exclusive): " + ", ".join(focus_tags)
            )
        roles = format_roles_block(role_ids)
        if roles:
            parts.append(roles)
        if existing_objectives_summary.strip():
            parts.append("Existing objectives:\n" + existing_objectives_summary.strip())
        if memory_block.strip():
            parts.append(memory_block.strip())
        if prior_plan.strip():
            parts.append("Prior plan excerpt:\n" + prior_plan.strip()[:4000])
        return self.wrap_messages(parts)

    def format_result(
        self,
        raw_llm_text: str,
        *,
        project_title: str = "adhoc",
        **_kwargs,
    ) -> str:
        raw = self.message_text(raw_llm_text) or ""
        payload = parse_project_objectives(raw)
        objs = _objectives_from_payload(payload)
        if objs:
            return render_project_objectives(project_title, payload, objs)
        return raw or "(empty plan)"
