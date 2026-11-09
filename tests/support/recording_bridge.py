"""Test double for AgentBridgeBase. Not part of the orchestrator package."""

from __future__ import annotations

from orchestrator.agent.bridge_base import AgentBridgeBase

_NONE = frozenset(
    {
        "emit",
        "append_transcript",
        "request_phase_advance",
        "request_spawn_cap_approval",
    }
)


def _method(name: str):
    def method(self, *args, **kwargs):
        self.calls.append((name, args, dict(kwargs)))
        if name == "drain_operator_guidance":
            return []
        if name in _NONE:
            return None
        return ""

    method.__name__ = name
    return method


def _init(self) -> None:
    self.calls: list[tuple[str, tuple, dict]] = []


RecordingBridge = type(
    "RecordingBridge",
    (AgentBridgeBase,),
    {
        "__doc__": "Records every port call.",
        "__module__": __name__,
        "__init__": _init,
        **{name: _method(name) for name in AgentBridgeBase.__abstractmethods__},
    },
)
