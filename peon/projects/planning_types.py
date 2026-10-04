"""Planning dataclasses and objective-plan validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from orchestrator.crew.roles.registry import RoleRegistry, analyzer_role, manager_role


@dataclass
class PlanDraft:
    """LLM plan output before DB persist."""

    mode: str
    plan_text: str
    role_ids: list[str] = field(default_factory=list)
    objectives_payload: list[dict[str, Any]] = field(default_factory=list)
    description: str = ""
    title: str = "adhoc"
    summary: str = ""
    in_scope: list[dict[str, str]] = field(default_factory=list)
    exclusions: list[dict[str, str]] = field(default_factory=list)
    authorization: str = ""
    preferred_tags: list[str] = field(default_factory=list)


@dataclass
class PlanResult:
    job: Job | None
    plan_text: str
    project: Project | None = None
    objectives: list | None = None
    role_ids: list[str] | None = None


def validate_project_objectives(objectives_payload: list[dict[str, Any]]) -> None:
    """Reject plans that cannot create a runnable CrewAI objective graph."""

    registry = RoleRegistry.shared()
    roles = registry.list_roles()
    if not roles:
        raise ValueError(
            f"No CrewAI roles found in {registry.roles_dir()}; "
            "package or mount roles/ into the web and worker services"
        )
    manager = manager_role(registry)
    analyzer = analyzer_role(registry)
    if manager is None or analyzer is None:
        missing = []
        if manager is None:
            missing.append("engagement manager (allow_delegation, empty reports_to)")
        if analyzer is None:
            missing.append("reporting bookend (capabilities include report)")
        raise ValueError("CrewAI role catalog is missing: " + ", ".join(missing))

    rows = [row for row in objectives_payload if isinstance(row, dict)]
    if not rows:
        raise ValueError("Planner returned no objectives")

    known = {role.id for role in roles}
    invalid: list[str] = []
    for index, row in enumerate(rows, start=1):
        if not str(row.get("title") or "").strip():
            raise ValueError(f"Planner objective {index} has no title")
        role_id = str(row.get("role_id") or "").strip()
        if not role_id:
            raise ValueError(f"Planner objective {index} has no role_id")
        if role_id not in known and role_id not in invalid:
            invalid.append(role_id)
    if invalid:
        raise ValueError(
            "Planner used unknown CrewAI role id(s): " + ", ".join(invalid)
        )
    if str(rows[0].get("role_id") or "").strip() != manager.id:
        raise ValueError(f"First objective must use manager role {manager.id}")
    if str(rows[-1].get("role_id") or "").strip() != analyzer.id:
        raise ValueError(f"Last objective must use analyzer role {analyzer.id}")

