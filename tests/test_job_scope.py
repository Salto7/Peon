"""ContextVar binding for the active job. Captures the unbound defaults PR-2 removes."""

from __future__ import annotations

import pytest

from orchestrator.agent.bridge_base import AgentBridgeBase
from orchestrator.agent.config import AgentRunConfig
from orchestrator.agent.job import JobScope, bind_job, get_agent_config, get_job


def test_bind_job_nests_and_resets():
    outer = JobScope(job_id="outer", bridge=object())
    inner = JobScope(job_id="inner", bridge=object())
    outer_cfg = AgentRunConfig(max_iterations=7)
    inner_cfg = AgentRunConfig(max_iterations=3)

    with pytest.raises(RuntimeError, match="no agent job scope bound"):
        get_job()

    with bind_job(outer, outer_cfg):
        assert get_job() is outer
        assert get_agent_config() == outer_cfg
        with bind_job(inner, inner_cfg):
            assert get_job() is inner
            assert get_job().job_id == "inner"
            assert get_agent_config() == inner_cfg
        assert get_job() is outer
        assert get_agent_config() == outer_cfg

    with pytest.raises(RuntimeError, match="no agent job scope bound"):
        get_job()


def test_unbound_agent_config_returns_default():
    """Current unbound config is a default AgentRunConfig. PR-2 makes this raise."""
    assert get_agent_config() == AgentRunConfig()


def test_scope_without_bridge_uses_unconfigured_bridge():
    """A scope can be built with no bridge argument. PR-2 makes bridge required.

    The class name of that default is not part of this contract.
    """
    scope = JobScope(job_id="plain")
    assert isinstance(scope.bridge, AgentBridgeBase)
    scope.bridge.emit("status", "dropped")
    assert scope.bridge.assert_command_allowed("id") == ""
    with pytest.raises(RuntimeError, match="spawn_agent bridge not configured"):
        scope.bridge.spawn_agent(title="t", description="d")
    with pytest.raises(RuntimeError, match="wait_agents bridge not configured"):
        scope.bridge.wait_agents()
