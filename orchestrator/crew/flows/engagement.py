"""Build a hierarchical engagement Crew from ROLE.yaml hierarchy."""

from __future__ import annotations

from typing import Any

from orchestrator.crew.roles.factory import build_crew_agent
from orchestrator.crew.roles.hierarchy import (
    analyzer_role,
    manager_role,
    specialists_for,
)
from orchestrator.crew.roles.registry import RoleRegistry


def build_engagement_crew(
    *,
    brief: str,
    role_ids: tuple[str, ...] | list[str] | None = None,
    replan_note: str = "",
    max_iterations: int | None = None,
) -> Any:
    """Return a CrewAI ``Crew`` (hierarchical + planning). Lazy-imports crewai."""
    try:
        from crewai import Crew, Process, Task
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "crewai is required for AGENT_MODULE=crewai "
            "(pip install 'crewai>=1.0.0')"
        ) from exc

    reg = RoleRegistry.shared()
    manager_spec = manager_role(reg)
    analyzer_spec = analyzer_role(reg)
    if manager_spec is None:
        raise RuntimeError(
            "No manager role in roles/ (need allow_delegation: true and empty reports_to)"
        )
    if analyzer_spec is None:
        raise RuntimeError(
            "No analyzer role in roles/ (need capabilities including 'report')"
        )

    specialist_specs = specialists_for(role_ids, reg=reg)
    manager = build_crew_agent(manager_spec, max_iterations=max_iterations)
    specialists = [
        build_crew_agent(s, max_iterations=max_iterations) for s in specialist_specs
    ]
    analyzer = build_crew_agent(analyzer_spec, max_iterations=max_iterations)

    specialist_lines = ", ".join(s.id for s in specialist_specs) or "(none preselected)"
    plan_extra = ""
    if (replan_note or "").strip():
        plan_extra = (
            "\n\nOPERATOR REPLAN / STEER:\n"
            f"{replan_note.strip()[:4000]}\n"
            "Revise the remaining plan accordingly under RoE.\n"
        )

    engagement_task = Task(
        description=(
            "Authorized Peon engagement.\n\n"
            f"BRIEF:\n{(brief or '').strip()[:6000]}\n"
            f"{plan_extra}\n"
            f"Available specialists (reports_to={manager_spec.id}): {specialist_lines}\n"
            "As engagement manager:\n"
            "1. Call check_inbox, then roe_status; do not expand scope. "
            "Check the inbox again between major actions.\n"
            "2. Decide which specialist roles to use from those available "
            "(match brief to their goals/tags).\n"
            "3. Delegate to specialists; on failure, replan and retry once.\n"
            f"4. When probing is done (or blocked), hand off to {analyzer_spec.id} "
            "for findings/report synthesis.\n"
            "Specialists must assert_in_scope before networked probes.\n"
        ),
        expected_output=(
            "A short engagement summary: what was attempted, key findings "
            "references, blockers, and confirmation that the Analyzer produced "
            "report notes."
        ),
        agent=manager,
    )
    analyze_task = Task(
        description=(
            "Synthesize existing findings and workspace evidence into report "
            "notes. Do not run new probes. Use list_findings and write_report_note."
        ),
        expected_output=(
            "Structured report notes covering executive summary, findings, "
            "and RoE/sandbox context."
        ),
        agent=analyzer,
        context=[engagement_task],
    )

    # Hierarchical crews: manager_agent is separate from agents list.
    kwargs: dict[str, Any] = {
        "agents": [*specialists, analyzer],
        "tasks": [engagement_task, analyze_task],
        "process": Process.hierarchical,
        "manager_agent": manager,
        "verbose": False,
        "planning": True,
    }
    try:
        return Crew(**kwargs)
    except TypeError:
        kwargs.pop("planning", None)
        try:
            return Crew(**kwargs)
        except TypeError:
            # Older crewai: manager must live in agents without manager_agent=
            kwargs.pop("manager_agent", None)
            kwargs["agents"] = [manager, *specialists, analyzer]
            return Crew(**kwargs)
