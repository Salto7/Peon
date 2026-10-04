"""Control-plane and multi-agent tools for CrewAI roles."""

from __future__ import annotations

from orchestrator.crew.runtime_support import drain_agent_inbox
from orchestrator.crew.tools.decorators import crew_tool


def _scope():
    from orchestrator.agent.job import get_job

    return get_job()


@crew_tool("check_inbox", "Read new operator instructions and peer-agent messages.")
def check_inbox() -> str:
    return drain_agent_inbox() or "No new operator or peer messages."


@crew_tool("list_objectives", "List project objectives and their current status.")
def list_objectives() -> str:
    return _scope().bridge.list_objectives()


@crew_tool("update_objective_status", "Update an objective status and optional note.")
def update_objective_status(seq: int, status: str, note: str = "") -> str:
    return _scope().bridge.update_objective_status(int(seq), status, note)


@crew_tool("record_findings", "Record multiple findings from a JSON list.")
def record_findings(findings_json: str) -> str:
    return _scope().bridge.record_findings(findings_json)


@crew_tool("list_agents", "List other jobs working on the same objective.")
def list_agents() -> str:
    return _scope().bridge.list_agents()


@crew_tool("send_agent_message", "Send a message to a peer job on this objective.")
def send_agent_message(
    body: str,
    to_job_id: str = "",
    message_type: str = "inform",
    artifact_refs: str = "",
) -> str:
    refs = [part.strip() for part in artifact_refs.split(",") if part.strip()]
    return _scope().bridge.send_message(
        to_job_id=to_job_id,
        type=(message_type or "inform").strip().lower(),
        body=body,
        artifact_refs=refs,
    )


@crew_tool("spawn_agent", "Spawn a peer or child CrewAI role job.")
def spawn_agent(
    title: str,
    description: str,
    role_id: str = "",
    link: str = "peer",
) -> str:
    from orchestrator.agent.job import get_agent_config

    scope = _scope()
    kind = (link or "peer").strip().lower()
    if kind not in {"peer", "child"}:
        return "Error: link must be 'peer' or 'child'"
    if kind == "child" and scope.depth >= get_agent_config().max_subagent_depth:
        return (
            "Error: child depth limit "
            f"({get_agent_config().max_subagent_depth})"
        )
    try:
        job_id = scope.bridge.spawn_agent(
            title=title,
            description=description,
            role_ids=[role_id.strip()] if role_id.strip() else None,
            link=kind,
        )
    except Exception as exc:
        return f"Error spawning agent: {exc}"
    scope.bridge.emit(
        "log",
        f"spawned {kind} agent {job_id}: {title}",
        metadata={"event": "spawn_agent", "link": kind, "job_id": job_id},
    )
    return job_id if job_id.startswith("Error") else f"spawned {kind} job {job_id}"


@crew_tool("wait_for_agents", "Check child-agent status without blocking the worker.")
def wait_for_agents(timeout_seconds: int = 600, job_ids: str = "") -> str:
    ids = [job_id.strip() for job_id in job_ids.split(",") if job_id.strip()] or None
    try:
        return _scope().bridge.wait_agents(
            job_ids=ids,
            timeout_seconds=int(timeout_seconds),
        )
    except Exception as exc:
        return f"Error waiting for agents: {exc}"


@crew_tool(
    "propose_agents",
    "Use objective context to propose and spawn focused peer CrewAI roles.",
)
def propose_agents(context_notes: str = "", max_agents: int = 4) -> str:
    return _scope().bridge.propose_agents(
        context_notes=context_notes,
        max_agents=int(max_agents or 4),
    )
