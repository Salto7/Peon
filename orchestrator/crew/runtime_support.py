"""Runtime hooks shared by CrewAI tools and kickoff paths."""

from __future__ import annotations

from orchestrator.agent.job import JobScope, get_agent_config, get_job
from orchestrator.crew.checkpoint import record_checkpoint_event
from orchestrator.prompts import AGENT_RECOVER_NUDGE


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
    record_checkpoint_event(active, "agent_inbox", text)
    return text


def augment_tool_result(result: object, *, scope: JobScope | None = None) -> str:
    """Attach newly arrived inbox messages and capped recovery guidance."""
    active = scope or get_job()
    text = str(result if result is not None else "")
    additions: list[str] = []

    inbox = drain_agent_inbox(active)
    if inbox:
        additions.append("NEW AGENT INBOX — apply before your next action:\n" + inbox)

    lowered = text.lstrip().lower()
    failed = lowered.startswith(("error", "denied", "failed"))
    if failed:
        extras = active.extras
        used = int(extras.get("_failure_replans") or 0)
        maximum = get_agent_config().max_failure_replans
        if used < maximum:
            extras["_failure_replans"] = used + 1
            additions.append(AGENT_RECOVER_NUDGE)
            active.bridge.emit(
                "log",
                f"tool failure recovery {used + 1}/{maximum}",
                metadata={
                    "event": "agent_recover",
                    "failure_replans": used + 1,
                    "max_failure_replans": maximum,
                },
            )
        else:
            additions.append(
                f"Failure recovery limit reached ({maximum}); do not repeat this call."
            )
        record_checkpoint_event(active, "tool_error", text)

    if not additions:
        return text
    return text + "\n\n" + "\n\n".join(additions)
