"""CrewAI project orchestration (Peon-crewAI branch).

- ``registry`` — ``AGENT_MODULE`` resolution (job runtime)
- ``roles`` — platform role store + affinity skill decoration + CrewAI agent factory
- ``tools`` — sandbox / RoE / findings tool allowlists
- ``runtimes`` — job role runtime, which expands into the project crew
  (``runtimes.project_crewai.run_project_crew``) when the manager role
  requests it
"""

from orchestrator.crew.registry import (
    agent_module_id,
    get_job_runtime,
    reset_agent_modules,
)
from orchestrator.crew.roles import RoleRegistry, RoleSpec, build_crew_agent

__all__ = [
    "RoleRegistry",
    "RoleSpec",
    "agent_module_id",
    "build_crew_agent",
    "get_job_runtime",
    "reset_agent_modules",
]
