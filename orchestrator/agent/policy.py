"""Which capability tools a Job may bind from loaded Peon skills."""

from __future__ import annotations

from orchestrator.capabilities.registry import REGISTRY
from orchestrator.skills.misc.registry import SkillRegistry

_BASE = frozenset(
    {
        "sandbox_setup",
        "sandbox_status",
        "provision_cli",
        "skills_list",
        "skill_view",
    }
)

def resolve_tool_names(skill_names: list[str]) -> set[str]:
    """Union of skill ``allowed-tools`` ∩ registered tools, plus sandbox base set."""
    registered = set(REGISTRY.tool_map())
    allowed: set[str] = set(_BASE) & registered
    reg = SkillRegistry.shared()
    for raw in skill_names or []:
        skill = reg.load_skill(str(raw).strip())
        if skill is None:
            continue
        for name in skill.tools or []:
            n = str(name).strip()
            if n in registered:
                allowed.add(n)
    return allowed
