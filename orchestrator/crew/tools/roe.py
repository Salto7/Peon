"""RoE tools — scope is enforced via the control-plane bridge."""

from __future__ import annotations

from orchestrator.crew.tools.decorators import crew_tool


@crew_tool("roe_status", "Show Rules of Engagement summary for this project.")
def roe_status() -> str:
    from orchestrator.agent.job import get_job

    return get_job().bridge.roe_summary()


@crew_tool(
    "assert_in_scope",
    "Check whether a target value is authorized under active RoE before probing.",
)
def assert_in_scope(target: str) -> str:
    from orchestrator.agent.job import get_job

    value = (target or "").strip()
    if not value:
        return "Error: empty target."
    reason = get_job().bridge.assert_in_scope(value)
    if reason:
        return f"DENIED: {reason}"
    return f"ALLOWED: {value} is in-scope under current RoE."
