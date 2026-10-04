"""Findings tools — persist via AgentBridgeBase."""

from __future__ import annotations

from orchestrator.crew.tools.decorators import crew_tool


@crew_tool("record_finding", "Record a structured engagement finding.")
def record_finding(
    title: str,
    summary: str = "",
    severity: str = "info",
    kind: str = "",
) -> str:
    from orchestrator.agent.job import get_job

    return get_job().bridge.record_finding(
        title=title,
        description=summary,
        summary=summary,
        severity=severity,
        kind=kind,
    )


@crew_tool("list_findings", "List recorded findings for this project.")
def list_findings(kind: str = "") -> str:
    from orchestrator.agent.job import get_job

    return get_job().bridge.list_findings(kind=kind)


@crew_tool(
    "write_report_note",
    "Append a report note for the analyzer (workspace findings path).",
)
def write_report_note(section: str, body: str) -> str:
    from orchestrator.agent.job import get_job

    return get_job().bridge.record_finding(
        title=f"report:{section}"[:200],
        description=(body or "")[:4000],
        summary=(body or "")[:4000],
        severity="info",
        kind="report",
    )
