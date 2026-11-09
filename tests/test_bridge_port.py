"""AgentBridgeBase is a real interface. The unconfigured default is gone."""

from __future__ import annotations

import pytest

from orchestrator.agent.bridge_base import AgentBridgeBase
from orchestrator.agent.job import JobScope
from tests.support.recording_bridge import RecordingBridge


def test_agent_bridge_base_cannot_be_instantiated():
    with pytest.raises(TypeError):
        AgentBridgeBase()


def test_subclass_missing_one_method_raises_type_error():
    missing = "emit"
    namespace = {"__module__": __name__}
    for name in AgentBridgeBase.__abstractmethods__:
        if name == missing:
            continue

        def _method(self, *args, **kwargs):
            del self, args, kwargs
            return None

        _method.__name__ = name
        namespace[name] = _method
    partial = type("PartialBridge", (AgentBridgeBase,), namespace)
    with pytest.raises(TypeError):
        partial()


def test_job_scope_without_bridge_raises_type_error():
    with pytest.raises(TypeError):
        JobScope(job_id="plain")


def test_recording_bridge_is_a_complete_port():
    bridge = RecordingBridge()
    scope = JobScope(job_id="recorded", bridge=bridge)
    assert scope.bridge is bridge
    assert bridge.emit("status", "hello", metadata={"k": "v"}) is None
    assert bridge.calls == [("emit", ("status", "hello"), {"metadata": {"k": "v"}})]
    assert "orchestrator" not in type(bridge).__module__
