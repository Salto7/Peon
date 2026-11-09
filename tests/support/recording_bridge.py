"""Test double for AgentBridgeBase. Not part of the orchestrator package."""

from __future__ import annotations

import abc

from orchestrator.agent.bridge_base import AgentBridgeBase

_NONE_RESULTS = frozenset(
    {
        "emit",
        "append_transcript",
        "request_phase_advance",
        "request_spawn_cap_approval",
    }
)


def _recorded(name: str):
    def method(self, *args, **kwargs):
        self.calls.append((name, args, dict(kwargs)))
        if name == "drain_operator_guidance":
            return []
        if name in _NONE_RESULTS:
            return None
        return ""

    method.__name__ = name
    return method


class RecordingBridge(AgentBridgeBase):
    """Records every port call. Implements the whole ABC so tests can bind a job."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []


for _name in AgentBridgeBase.__abstractmethods__:
    setattr(RecordingBridge, _name, _recorded(_name))

abc.update_abstractmethods(RecordingBridge)
