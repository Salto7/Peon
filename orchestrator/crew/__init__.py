"""CrewAI project orchestration (Peon-crewAI branch).

- ``runtime_base`` — project Flow DTOs + ``CrewRuntimeBase``
- ``registry`` — ``AGENT_MODULE`` resolution (job + crew)
- ``roles`` — ROLE.yaml catalog + CrewAI agent factory
- ``tools`` — sandbox / RoE / findings tool allowlists
- ``runtimes`` — job role runtime + project Flow (noop until Phase C)
"""

from orchestrator.crew.registry import (
    agent_module_id,
    get_crew_runtime,
    get_job_runtime,
    reset_agent_modules,
)
from orchestrator.crew.roles import RoleRegistry, RoleSpec, build_crew_agent
from orchestrator.crew.runtime_base import (
    CrewRunRequest,
    CrewRunResult,
    CrewRuntimeBase,
)
from orchestrator.crew.runtimes import NoopCrewRuntime

__all__ = [
    "CrewRunRequest",
    "CrewRunResult",
    "CrewRuntimeBase",
    "NoopCrewRuntime",
    "RoleRegistry",
    "RoleSpec",
    "agent_module_id",
    "build_crew_agent",
    "get_crew_runtime",
    "get_job_runtime",
    "reset_agent_modules",
]
