"""Public agent API.

Layers:
- ``config`` — run caps
- ``bridge_base`` / ``bridges`` / ``job`` — control-plane adapter + per-Job scope
- ``runtime_base`` / ``runtimes`` — one Job tool loop (LangGraph + checkpoints)
- ``graph`` — LangGraph StateGraph only
- ``propose`` — LLM specialist proposals (control plane spawns Jobs)
- ``messaging_base`` — A2A-ready DTOs (control plane persistence)

Multi-agent orchestration lives in Peon Jobs, not inside LangGraph.
"""

from orchestrator.agent.bridge_base import AgentBridgeBase, AgentLink
from orchestrator.agent.bridges import NullAgentBridge
from orchestrator.agent.config import AgentRunConfig, agent_run_config_from_mapping
from orchestrator.agent.job import JobScope, bind_job, get_agent_config, get_job
from orchestrator.agent.runtime_base import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeBase,
    get_agent_runtime,
    run_agent,
    set_agent_runtime,
)
from orchestrator.agent.runtimes import LangGraphAgentRuntime

__all__ = [
    "AgentBridgeBase",
    "AgentLink",
    "AgentRunConfig",
    "AgentRunRequest",
    "AgentRunResult",
    "AgentRuntimeBase",
    "JobScope",
    "LangGraphAgentRuntime",
    "NullAgentBridge",
    "agent_run_config_from_mapping",
    "bind_job",
    "get_agent_config",
    "get_agent_runtime",
    "get_job",
    "run_agent",
    "set_agent_runtime",
]
