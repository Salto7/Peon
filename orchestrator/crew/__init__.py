"""CrewAI project orchestration (Peon-crewAI branch).

- ``runtime_base`` — project Flow DTOs + ``CrewOrchestratorBase``
- ``registry`` — ``AGENT_MODULE`` resolution (job runtime)
- ``roles`` — platform role store + affinity skill decoration + CrewAI agent factory
- ``tools`` — sandbox / RoE / findings tool allowlists
- ``runtimes`` — job role runtime + ``ProjectCrewOrchestrator`` Flow
"""

from orchestrator.crew.registry import (
    agent_module_id,
    get_job_runtime,
    reset_agent_modules,
)
from orchestrator.crew.roles import RoleRegistry, RoleSpec, build_crew_agent
from orchestrator.crew.runtime_base import (
    CrewOrchestratorBase,
    CrewRunRequest,
    CrewRunResult,
)

__all__ = [
    "CrewRunRequest",
    "CrewRunResult",
    "CrewOrchestratorBase",
    "RoleRegistry",
    "RoleSpec",
    "agent_module_id",
    "build_crew_agent",
    "get_job_runtime",
    "reset_agent_modules",
]
