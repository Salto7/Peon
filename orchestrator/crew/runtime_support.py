"""Runtime hooks shared by CrewAI tools and kickoff paths."""

from __future__ import annotations

from typing import Any

from orchestrator.agent.job import JobScope, get_job


def drain_agent_inbox(scope: JobScope | None = None) -> str:
    """Drain operator and peer queues, stream the event, and return agent context."""
    active = scope or get_job()
    notes: list[str] = []
    for label, drain in (
        ("operator", active.bridge.drain_operator_guidance),
        ("peer", active.bridge.drain_peer_messages),
    ):
        try:
            notes.extend(str(note).strip() for note in (drain() or []) if str(note).strip())
        except Exception as exc:
            active.bridge.emit("error", f"{label} guidance drain failed: {exc}")
    if not notes:
        return ""
    text = "\n\n".join(notes)[:8000]
    active.bridge.emit(
        "log",
        text[:2000],
        metadata={"event": "agent_inbox", "role": "assistant"},
    )
    return text


def augment_tool_result(result: Any, *, scope: JobScope | None = None) -> Any:
    """Deliver queued guidance with a tool result without classifying its content."""
    inbox = drain_agent_inbox(scope or get_job())
    if not inbox:
        return result
    note = "NEW AGENT INBOX — apply before your next action:\n" + inbox
    try:
        from crewai.tools.tool_failure import ToolFailure

        if isinstance(result, ToolFailure):
            return result.model_copy(update={"message": f"{result.message}\n\n{note}"})
    except ImportError:  # pragma: no cover - guarded by the CrewAI runtime dependency
        pass
    return f"{result if result is not None else ''}\n\n{note}".strip()


def crew_step_callback(step: Any) -> None:
    """Generic CrewAI step telemetry; serializable for native checkpoints."""
    scope = get_job()
    label = type(step).__name__
    content = str(getattr(step, "log", None) or getattr(step, "text", None) or step)
    scope.bridge.emit(
        "log",
        content[:2000],
        metadata={"event": "crewai_step", "step_type": label},
    )


def crew_task_callback(output: Any) -> None:
    """Generic task-completion telemetry; serializable for native checkpoints."""
    scope = get_job()
    task_name = str(getattr(output, "name", None) or getattr(output, "description", ""))
    raw = str(getattr(output, "raw", None) or output or "")
    scope.bridge.emit(
        "status",
        f"CrewAI task completed: {task_name[:160] or '(unnamed)'}",
        metadata={"event": "crewai_task_done", "output": raw[:2000]},
    )
