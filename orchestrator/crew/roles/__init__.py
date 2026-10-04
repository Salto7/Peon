"""Role catalog: ROLE.yaml → RoleSpec → CrewAI Agent factory."""

from orchestrator.crew.roles.model import RoleSpec, build_crew_agent
from orchestrator.crew.roles.registry import (
    RoleRegistry,
    analyzer_role,
    engagement_bookends,
    engagement_end_role,
    engagement_start_role,
    load_role_file,
    manager_role,
    specialists_for,
)

__all__ = [
    "RoleRegistry",
    "RoleSpec",
    "analyzer_role",
    "build_crew_agent",
    "engagement_bookends",
    "engagement_end_role",
    "engagement_start_role",
    "load_role_file",
    "manager_role",
    "specialists_for",
]
