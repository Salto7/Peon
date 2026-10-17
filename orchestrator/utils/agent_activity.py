"""One-line agent status for the project graph.

Built from job state plus the latest structured stream event
(``metadata.event`` / ``metadata.payload``). Callers do not scrape message text.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from orchestrator.utils.stream_events import (
    EVENT_CALL_MCP_TOOL,
    EVENT_LIST_MCP_RESOURCES,
    EVENT_LIST_MCP_TOOLS,
    EVENT_PROVISION_CLI,
    EVENT_READ_ARTIFACT,
    EVENT_READ_MCP_RESOURCE,
    EVENT_RECORD_FINDING,
    EVENT_REPORT_NOTE,
    EVENT_RUN_CLI,
    EVENT_RUN_PERIODIC,
    EVENT_SPAWN_WORK,
    EVENT_WRITE_ARTIFACT,
)

_TERMINAL = {
    "completed": "Completed",
    "failed": "Failed",
    "cancelled": "Cancelled",
}

_IN_FLIGHT = frozenset(
    {
        EVENT_RUN_CLI,
        EVENT_RUN_PERIODIC,
        EVENT_CALL_MCP_TOOL,
        EVENT_PROVISION_CLI,
        EVENT_LIST_MCP_TOOLS,
        EVENT_LIST_MCP_RESOURCES,
        EVENT_READ_MCP_RESOURCE,
    }
)

_TOOL_DONE = frozenset(
    {
        EVENT_RUN_CLI,
        EVENT_RUN_PERIODIC,
        EVENT_CALL_MCP_TOOL,
        EVENT_LIST_MCP_TOOLS,
        EVENT_LIST_MCP_RESOURCES,
        EVENT_READ_MCP_RESOURCE,
    }
)

_SOURCE_SUFFIXES = (
    ".py",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".sh",
    ".bash",
    ".go",
    ".rs",
    ".rb",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".cs",
)


def describe_activity(
    *,
    status: str,
    event: str = "",
    payload: Mapping[str, Any] | None = None,
    idle_reason: str = "",
    waiting_on: list | None = None,
    children_active: int = 0,
) -> dict[str, Any]:
    """Return ``{"label", "live"}`` for one agent node."""
    state = (status or "").strip().lower()
    name = (event or "").strip().lower()
    body = dict(payload or {})
    phase = str(body.get("phase") or "").strip().lower()
    kids = max(0, int(children_active or 0))

    if state in _TERMINAL:
        return _view(_TERMINAL[state], False)
    if state == "paused":
        return _view("Paused", False)
    if state == "pending":
        return _view("Queued", False)
    if state == "idle":
        label, live = _idle_label(
            idle_reason=idle_reason,
            waiting_on=waiting_on or [],
            children_active=kids,
        )
        return _view(label, live)

    return _view(
        _running_label(name, body, phase, kids) or "Working",
        True,
    )


def _view(label: str, live: bool) -> dict[str, Any]:
    text = " ".join(str(label or "").split())
    return {"label": text[:80], "live": bool(live and text)}


def _idle_label(
    *,
    idle_reason: str,
    waiting_on: list,
    children_active: int,
) -> tuple[str, bool]:
    if children_active:
        return "Waiting for sub-agent", True
    objectives = _objective_wait(waiting_on)
    if objectives:
        return objectives, True
    reason = (idle_reason or "").strip().lower()
    if reason == "dependencies":
        return "Waiting for dependencies", True
    if reason == "message":
        return "Waiting for a message", True
    if reason == "governance":
        return "Watching the engagement", True
    return "Idle", False


def _objective_wait(waiting_on: list) -> str:
    seqs: list[str] = []
    for item in waiting_on or []:
        text = str(item).strip()
        if not text:
            continue
        seqs.append(text if text.upper().startswith("OBJ-") else f"OBJ-{text}")
    if not seqs:
        return ""
    shown = ", ".join(seqs[:3])
    if len(seqs) > 3:
        shown += f" +{len(seqs) - 3}"
    return f"Waiting for {shown}"


def _running_label(
    event: str,
    payload: Mapping[str, Any],
    phase: str,
    children_active: int,
) -> str:
    if event in _IN_FLIGHT and phase != "done":
        return _in_flight_label(event, payload)
    if event == EVENT_REPORT_NOTE:
        return "Updating report"
    if event == EVENT_RECORD_FINDING:
        return "Recording a finding"
    if event == EVENT_READ_ARTIFACT:
        return _file_label(str(payload.get("path") or ""), writing=False)
    if event == EVENT_WRITE_ARTIFACT:
        return _file_label(str(payload.get("path") or ""), writing=True)
    if event == EVENT_SPAWN_WORK:
        return "Waiting for sub-agent"
    if event in _TOOL_DONE and phase == "done":
        return "Analyzing tool results"
    if children_active:
        return "Waiting for sub-agent"
    if event == "crewai_role_start":
        return "Starting"
    if event == "crewai_role_done":
        return "Finishing"
    return ""


def _in_flight_label(event: str, payload: Mapping[str, Any]) -> str:
    if event in {EVENT_RUN_CLI, EVENT_RUN_PERIODIC}:
        return _running_command(payload)
    if event == EVENT_CALL_MCP_TOOL:
        tool = str(payload.get("tool") or "").strip()
        return f"Running {tool}" if tool else "Running an MCP tool"
    if event == EVENT_PROVISION_CLI:
        binary = str(payload.get("binary") or "").strip()
        return f"Installing {binary}" if binary else "Installing a tool"
    if event == EVENT_LIST_MCP_TOOLS:
        return "Listing tools"
    if event in {EVENT_LIST_MCP_RESOURCES, EVENT_READ_MCP_RESOURCE}:
        return "Reading a resource"
    return "Working"


def _running_command(payload: Mapping[str, Any]) -> str:
    skill = str(payload.get("skill_id") or "").strip()
    if skill:
        return f"Running {skill}"
    cli = str(payload.get("cli") or "").strip()
    if cli:
        return f"Running {cli}"
    command = str(payload.get("command") or "").strip()
    token = command.split()[0].rsplit("/", 1)[-1] if command else ""
    return f"Running {token}" if token else "Running a command"


def _file_label(path: str, *, writing: bool) -> str:
    name = str(path or "").strip().rstrip("/").rsplit("/", 1)[-1] or "a file"
    if writing:
        return f"Writing {name}"
    if name.lower().endswith(_SOURCE_SUFFIXES):
        return f"Analyzing {name}"
    return f"Reading {name}"
