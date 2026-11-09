"""Public agent API.

Layers:
- ``config`` — run caps
- ``bridge_base`` / ``job`` — control-plane adapter + per-Job scope
- ``runtime_base`` — Job runtime ABC; concrete backend via ``registry``
- ``backends`` — one package per framework (crewai)
- ``propose`` — LLM specialist proposals (control plane spawns Jobs)

Role, tool, and memory domain stays in ``orchestrator.crew``.
"""

from orchestrator.agent.bridge_base import AgentBridgeBase, AgentLink
from orchestrator.agent.config import AgentRunConfig, agent_run_config_from_mapping
from orchestrator.agent.job import JobScope, bind_job, get_agent_config, get_job
from orchestrator.agent.runtime_base import (
    AgentExecutorBase,
    AgentRunRequest,
    AgentRunResult,
    get_agent_runtime,
    run_agent,
    set_agent_runtime,
)

__all__ = [
    "AgentBridgeBase",
    "AgentLink",
    "AgentRunConfig",
    "AgentRunRequest",
    "AgentRunResult",
    "AgentExecutorBase",
    "JobScope",
    "agent_run_config_from_mapping",
    "bind_job",
    "get_agent_config",
    "get_agent_runtime",
    "get_job",
    "run_agent",
    "set_agent_runtime",
]
