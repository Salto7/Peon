"""Runtime hooks shared by CrewAI tools and kickoff paths."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from orchestrator.agent.job import JobScope, get_job


@dataclass(frozen=True)
class InboxContext:
    text: str = ""
    directive_kinds: frozenset[str] = frozenset()


def drain_agent_inbox_context(scope: JobScope | None = None) -> InboxContext:
    """Drain queues once and retain typed control-plane directive metadata."""
    active = scope or get_job()
    notes: list[str] = []
    directive_kinds: set[str] = set()
    try:
        guidance, kinds = active.bridge.drain_operator_directives()
        notes.extend(
            str(note).strip() for note in (guidance or []) if str(note).strip()
        )
        directive_kinds.update(str(kind).strip() for kind in (kinds or set()) if kind)
    except Exception as exc:
        active.bridge.emit("error", f"operator guidance drain failed: {exc}")
    try:
        notes.extend(
            str(note).strip()
            for note in (active.bridge.drain_peer_messages() or [])
            if str(note).strip()
        )
    except Exception as exc:
        active.bridge.emit("error", f"peer guidance drain failed: {exc}")
    if not notes:
        return InboxContext(directive_kinds=frozenset(directive_kinds))
    text = "\n\n".join(notes)[:8000]
    active.bridge.emit(
        "log",
        text[:2000],
        metadata={"event": "agent_inbox", "role": "assistant"},
    )
    return InboxContext(text=text, directive_kinds=frozenset(directive_kinds))


def drain_agent_inbox(scope: JobScope | None = None) -> str:
    """Drain operator and peer queues and return agent-facing context."""
    return drain_agent_inbox_context(scope).text


def augment_tool_result(result: Any, *, scope: JobScope | None = None) -> Any:
    """Deliver queued guidance with a tool result without classifying its content."""
    inbox = drain_agent_inbox(scope or get_job())
    if not inbox:
        return result
    note = "NEW AGENT INBOX — apply before your next action:\n" + inbox
    from crewai.tools.tool_failure import ToolFailure

    if isinstance(result, ToolFailure):
        return result.model_copy(update={"message": f"{result.message}\n\n{note}"})
    return f"{result if result is not None else ''}\n\n{note}".strip()


