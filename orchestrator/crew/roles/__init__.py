"""Role catalog: platform DB + affinity skills → RoleSpec → AgentSpec."""

from orchestrator.crew.roles.catalog import (
    DEFAULT_ROLES,
    ROLE_KINDS,
    RoleDef,
    catalog_role_ids,
    resolve_role_id,
)
from orchestrator.crew.roles.model import AgentSpec, RoleSpec, build_agent_spec
from orchestrator.crew.roles.registry import RoleRegistry

__all__ = [
    "DEFAULT_ROLES",
    "ROLE_KINDS",
    "RoleDef",
    "AgentSpec",
    "RoleRegistry",
    "RoleSpec",
    "build_agent_spec",
    "catalog_role_ids",
    "resolve_role_id",
]
