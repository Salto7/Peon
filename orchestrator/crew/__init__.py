"""Crew domain: roles, tools, routing, and memory.

CrewAI execution lives in ``orchestrator.agent.backends.crewai``.
``AGENT_MODULE`` resolution lives in ``orchestrator.agent.registry``.
"""

from orchestrator.crew.roles import AgentSpec, RoleRegistry, RoleSpec, build_agent_spec

__all__ = [
    "AgentSpec",
    "RoleRegistry",
    "RoleSpec",
    "build_agent_spec",
]
