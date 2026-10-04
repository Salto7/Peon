"""Role catalog: ROLE.yaml → RoleSpec → CrewAI Agent factory."""

from orchestrator.crew.roles.factory import build_crew_agent
from orchestrator.crew.roles.hierarchy import (
    analyzer_role,
    engagement_bookends,
    manager_role,
    specialists_for,
)
from orchestrator.crew.roles.model import RoleSpec
from orchestrator.crew.roles.registry import RoleRegistry

__all__ = [
    "RoleRegistry",
    "RoleSpec",
    "analyzer_role",
    "build_crew_agent",
    "engagement_bookends",
    "manager_role",
    "specialists_for",
]
