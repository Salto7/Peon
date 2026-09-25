"""Project-mode planner: objectives JSON → markdown."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from orchestrator.planning.base import BasePlanner
from orchestrator.skills.misc.utils import PROJECT_PLAN_END, PROJECT_PLAN_START
from orchestrator.utils.strings import extract_json

PROJECT_PLAN_SYSTEM = """You are the PROJECT planner for an authorized engagement orchestrator.

Mission: decompose the engagement into kill-chain OBJECTIVES that every job under
the project can share. You do not call tools and you do not execute. You produce
the shared project plan (objectives + dependencies), not a per-job Approach playbook.

## Hard constraints (RoE)
- Plan ONLY against in-scope subjects; never expand scope
- NEVER plan actions against exclusions
- If RoE / in-scope is missing or empty: minimal blueprint + passive/seed-driven
  objectives only; do not invent attack targets. Active network skills
  (e.g. network-scanner, http-prober) need at least one in-scope *value* (any type
  hint is fine — authorization is the string, not the label)
- Target type prefixes (person:, phone:, ip:, file:, malware:, …) are optional hints;
  discoveries are candidates — not authorized until promoted
- Prefer Ubuntu sandbox + skill-installed tools from the catalog — not a fixed distro
- Skills are system-wide; project data lives under workspace/ and findings/
- Findings are engagement discoveries about any subject class (with evidence), not
  job/objective/agent status

## Planning principles
- First objective MUST use skill_suggestion `blueprint` (confirm/refine `plans/latest.md`
  before any execution). Do not invent a different planner skill name.
- Start with recon/analysis after the blueprint unless the brief shows that work is done
- Keep 3–8 objectives total (including the mandatory blueprint + analyzer bookends);
  each must be independently verifiable and cheap to re-plan
- Prefer skill catalog order and documented prerequisites (blueprint → early work → follow-on → analyzer)
- Operator focus tags (if any) are a starting preference only — add objectives and
  skill_suggestion values for other phases when the project needs them
- Prefer skill_suggestion from the Skill catalog whose tags/categories fit the
  objective; never invent skill names. Every non-bookend objective MUST have a
  non-empty skill_suggestion from the catalog matching the work
  (network, OSINT, malware, source, reporting, …). Omit the objective entirely
  rather than leaving skill_suggestion empty.
- Optional profile_suggestion: a role name for the job that will run the objective
  (e.g. OSINT-lead, report-writer) when a roles list is provided; else leave empty
- depends_on uses 1-based indices in THIS objectives list
- Parallel only when objectives are truly independent; otherwise chain with depends_on
- One worker job per phase — no duplicate corroboration workers unless asked
- On revise: do not cancel objectives that already produced usable workspace/findings
  evidence — mark completed or leave; prefer update over cancel
- Exactly ONE reporting objective, LAST, with skill_suggestion `analyzer` — no duplicate
  pending reporting clones and no analyzer/blueprint in the middle
- Reporting acceptance_criteria MUST require: write the sole findings/report.md as the
  primary deliverable; phase workers write findings/<phase>.md only (never report.md);
  keep phase artifacts as supporting docs (do not delete them)
- No ethics lectures, no prose outside JSON

## Quality bar for each objective
- title: short, verb-led (e.g. “Inventory in-scope subjects”)
- phase: recon | initial-access | post-exploit | reporting
- description: concrete actions on named in-scope subjects (tools/skills when known)
- acceptance_criteria: observable done condition an executor can check
- mitre: relevant technique ids when known; else []
- skill_suggestion: REQUIRED catalog skill id (never invent; never leave empty
  for executable objectives)
- profile_suggestion: role id or empty
- commands: 1–4 concrete calls that WOULD run for this objective (dry-run only —
  you never execute). Prefer `run_skill_script("<skill>", "scripts/…", …)` forms
  from the skill catalog; use `provision_cli("<binary>")` before missing CLIs;
  fill in-scope subjects; use `sandbox_setup()` when needed.
  Empty list only when the objective is pure planning/reporting with no tool call.

## Output
Return ONLY a JSON object (no markdown fences, no commentary) with this shape:
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
      "skill_suggestion": "<catalog skill id — required>",
      "profile_suggestion": "<role id or empty>",
      "commands": [
        "sandbox_setup()",
        "run_skill_script(\"<skill>\", \"scripts/run.py\", command=\"…\")"
      ]
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

    skill = str(obj.get("skill_suggestion") or "").strip()
    phase = str(obj.get("phase") or "").strip().lower()
    if skill in {PROJECT_PLAN_START, PROJECT_PLAN_END}:
        return True
    return phase == "reporting"


def bookend_project_objectives(objectives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure every project plan starts with blueprint and ends with analyzer."""

    raw = [dict(o) for o in (objectives or []) if isinstance(o, dict)]
    survivors: list[tuple[int, dict[str, Any]]] = []
    for idx, obj in enumerate(raw, start=1):
        if not _is_bookend_objective(obj):
            survivors.append((idx, obj))

    blueprint = {
        "title": "Project blueprint",
        "phase": "recon",
        "description": (
            "Confirm the shared project plan under plans/latest.md before execution."
        ),
        "acceptance_criteria": "plans/latest.md exists and reflects the engagement goal",
        "mitre": [],
        "depends_on": [],
        "skill_suggestion": PROJECT_PLAN_START,
        "profile_suggestion": "",
        "commands": [
            f'run_skill_script("{PROJECT_PLAN_START}", "scripts/run.py", command="")',
        ],
    }
    middle: list[dict[str, Any]] = []
    old_to_final: dict[int, int] = {}
    for old_idx, obj in survivors:
        final_idx = len(middle) + 2  # start skill occupies index 1
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
        skill = str(row.get("skill_suggestion") or "").strip()
        if not skill:
            # Unexecutable middle objectives poison the DAG — drop them.
            continue
        row["skill_suggestion"] = skill
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
        "acceptance_criteria": (
            "findings/report.md written as the sole project report"
        ),
        "mitre": [],
        "depends_on": [last_middle],
        "skill_suggestion": PROJECT_PLAN_END,
        "profile_suggestion": "",
        "commands": [
            f'run_skill_script("{PROJECT_PLAN_END}", "scripts/run.py", command="")',
        ],
    }
    return [blueprint, *middle, analyzer]


def bookend_skill_names(names: list[str] | None) -> list[str]:
    """Put plan-start first and plan-end last; drop duplicates of either."""

    bookends = {PROJECT_PLAN_START, PROJECT_PLAN_END}
    middle: list[str] = []
    seen: set[str] = set()
    for raw in names or []:
        name = str(raw or "").strip()
        if not name or name in bookends or name in seen:
            continue
        seen.add(name)
        middle.append(name)
    return [PROJECT_PLAN_START, *middle, PROJECT_PLAN_END]


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
        suggestion = getattr(obj, "skill_suggestion", "") or ""
        if suggestion:
            lines.append(f"   - Skill suggestion: `{suggestion}`")
        profile = getattr(obj, "profile_suggestion", "") or ""
        if profile:
            lines.append(f"   - Profile suggestion: `{profile}`")
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
                skill_suggestion=o.get("skill_suggestion") or "",
                profile_suggestion=o.get("profile_suggestion") or o.get("profile") or "",
                commands=list(o.get("commands") or []),
            )
        )
    return objs


class ProjectPlanner(BasePlanner):
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
        filtered_skills_index: str = "",
        focus_tags: list[str] | None = None,
        role_ids: list[str] | None = None,
        **_kwargs,
    ) -> list:
        parts = [
            f"Project: {project_title}",
            f"Summary: {(project_summary or '').strip() or '(none)'}",
            f"Job brief:\n{(description or '').strip()}",
            format_roe_block(roe),
        ]
        if filtered_skills_index.strip():
            parts.append(
                "Skill catalog (full — focus tags are preferences only):\n"
                + filtered_skills_index.strip()
            )
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
