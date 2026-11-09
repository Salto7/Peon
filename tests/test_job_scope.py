"""ContextVar binding for the active job."""

from __future__ import annotations

import pytest

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


def test_unbound_agent_config_raises():
    with pytest.raises(RuntimeError, match="no agent job config bound"):
        get_agent_config()


def test_scope_requires_a_bridge():
    with pytest.raises(TypeError):
        JobScope(job_id="plain")
