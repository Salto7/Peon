"""Build a hierarchical engagement Crew from ROLE.yaml hierarchy."""

from __future__ import annotations

from typing import Any, Tuple

from orchestrator.crew.roles.factory import build_crew_agent
from orchestrator.crew.roles.hierarchy import (
    analyzer_role,
    manager_role,
    specialists_for,
)
from orchestrator.crew.roles.registry import RoleRegistry
from orchestrator.crew.runtime_support import crew_step_callback, crew_task_callback


def require_meaningful_output(output: Any) -> Tuple[bool, Any]:
    """Generic task guardrail that rejects empty agent output."""
    raw = str(getattr(output, "raw", None) or output or "").strip()
    if raw:
        return True, output
    return False, "Task produced no usable output; revise the plan and try again."


def build_engagement_crew(
    *,
    role_ids: tuple[str, ...] | list[str] | None = None,
    max_iterations: int | None = None,
    max_replans: int = 2,
    max_execution_time: int | None = None,
    memory: Any | None = None,
    checkpoint: Any | None = None,
    output_log_file: str | None = None,
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
    agent_options = {
        "max_iterations": max_iterations,
        "max_replans": max_replans,
        "max_execution_time": max_execution_time,
    }
    manager = build_crew_agent(manager_spec, **agent_options)
    specialists = [
        build_crew_agent(s, **agent_options) for s in specialist_specs
    ]
    analyzer = build_crew_agent(analyzer_spec, **agent_options)

    specialist_lines = ", ".join(s.id for s in specialist_specs) or "(none preselected)"

    engagement_task = Task(
        description=(
            "Authorized Peon engagement.\n\n"
            "BRIEF:\n{brief}\n\n"
            "CURRENT OPERATOR CONTEXT:\n{operator_context}\n\n"
            f"Available specialists (reports_to={manager_spec.id}): {specialist_lines}\n"
            "As engagement manager:\n"
            "1. Review current operator guidance and Rules of Engagement using "
            "your available tools; do not expand scope.\n"
            "2. Decide which specialist roles to use from those available "
            "(match brief to their goals/tags).\n"
            "3. Delegate to specialists. Observe execution failures and revise "
            "the remaining plan within the configured replan budget.\n"
            f"4. When work is done or blocked, hand off to {analyzer_spec.label} "
            "for synthesis.\n"
            "Specialists must verify authorization scope before networked probes.\n"
        ),
        expected_output=(
            "A short engagement summary: what was attempted, key findings "
            "references, blockers, and confirmation that the Analyzer produced "
            "report notes."
        ),
        agent=manager,
        guardrail=require_meaningful_output,
        guardrail_max_retries=max(1, int(max_replans)),
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
        guardrail=require_meaningful_output,
        guardrail_max_retries=max(1, int(max_replans)),
    )

    # Hierarchical crews: manager_agent is separate from agents list.
    kwargs: dict[str, Any] = {
        "agents": [*specialists, analyzer],
        "tasks": [engagement_task, analyze_task],
        "process": Process.hierarchical,
        "manager_agent": manager,
        "verbose": False,
        "planning": True,
        "memory": memory,
        "checkpoint": checkpoint,
        "output_log_file": output_log_file,
        "step_callback": crew_step_callback,
        "task_callback": crew_task_callback,
    }
    return Crew(**kwargs)
