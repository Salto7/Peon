"""Register LangChain tools by name for ``allowed-tools`` filtering.

Called capabilities (not “tools”) to avoid confusion with ``tools/catalog``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

_REGISTERED = False


class CapabilityGroup(str, Enum):
    SANDBOX = "sandbox"
    SKILLS = "skills"
    ENGAGEMENT = "engagement"
    CORE = "core"


@dataclass(frozen=True)
class RegisteredCapability:
    group: CapabilityGroup
    tool: Any
    tags: frozenset[str] = field(default_factory=frozenset)


class CapabilityRegistry:
    def __init__(self) -> None:
        self._by_name: dict[str, RegisteredCapability] = {}

    def register(
        self,
        group: CapabilityGroup,
        tool: Any,
        *,
        tags: Iterable[str] = (),
    ) -> Any:
        name = str(getattr(tool, "name", "") or "").strip()
        if not name:
            raise ValueError("Capability tool must have a name")
        if name in self._by_name:
            raise ValueError(f"Duplicate capability registration: {name!r}")
        self._by_name[name] = RegisteredCapability(
            group=group,
            tool=tool,
            tags=frozenset(str(t).strip() for t in tags if str(t).strip()),
        )
        return tool

    def get(self, name: str) -> RegisteredCapability | None:
        return self._by_name.get(name)

    def tool_map(self) -> dict[str, Any]:
        return {name: e.tool for name, e in self._by_name.items()}

    def names(
        self,
        *,
        groups: Iterable[CapabilityGroup] | None = None,
        exclude_tags: Iterable[str] = (),
        include: Iterable[str] | None = None,
        exclude: Iterable[str] = (),
    ) -> list[str]:
        wanted = set(groups) if groups is not None else None
        skip_tags = {str(t).strip() for t in exclude_tags if str(t).strip()}
        allow = {str(n).strip() for n in include} if include is not None else None
        deny = {str(n).strip() for n in exclude if str(n).strip()}
        out: list[str] = []
        for name, entry in self._by_name.items():
            if name in deny:
                continue
            if allow is not None and name not in allow:
                continue
            if wanted is not None and entry.group not in wanted:
                continue
            if entry.tags & skip_tags:
                continue
            out.append(name)
        return out


REGISTRY = CapabilityRegistry()

_GROUP_ORDER = (
    CapabilityGroup.SANDBOX,
    CapabilityGroup.SKILLS,
    CapabilityGroup.ENGAGEMENT,
)
_SKIP_AUTHORING_TAGS = frozenset({"watchdog", "periodic", "subagent"})
# catalog_wrapper: sandbox status/setup + provision_cli only.
# Exec still goes through run_skill_script, not free-form run_cli.
_CATALOG_SANDBOX = frozenset({"sandbox_setup", "sandbox_status", "provision_cli"})


def ensure_registered() -> CapabilityRegistry:
    """Import capability tools once so ``@capability`` side-effects populate REGISTRY."""
    global _REGISTERED
    if not _REGISTERED:
        import orchestrator.tools.llm_tools  # noqa: F401

        _REGISTERED = True
    return REGISTRY


def default_allowed_tools(*, mode: str = "catalog_wrapper") -> str:
    """``allowed-tools`` string from the live registry for skill authoring.

    - ``catalog_wrapper``: sandbox trio (setup/status/provision) + skills +
      engagement; skip watchdog/periodic/subagent tags.
    - ``capability``: same as catalog_wrapper plus ``run_cli`` (not run_code).
    """
    reg = ensure_registered()
    names: list[str] = []
    for group in _GROUP_ORDER:
        for name in sorted(reg.names(groups=(group,), exclude_tags=_SKIP_AUTHORING_TAGS)):
            entry = reg.get(name)
            if entry is None:
                continue
            if entry.group == CapabilityGroup.SANDBOX:
                if name in _CATALOG_SANDBOX:
                    names.append(name)
                elif mode == "capability" and name == "run_cli":
                    names.append(name)
                continue
            names.append(name)
    return " ".join(names)


def capability(group: CapabilityGroup, *, tags: Iterable[str] = ()):
    """Register a LangChain ``@tool`` under ``group``."""

    def decorator(tool: Any) -> Any:
        return REGISTRY.register(group, tool, tags=tags)

    return decorator


def get_tools_for_names(names: set[str] | frozenset[str]) -> list[Any]:
    ensure_registered()
    tools = REGISTRY.tool_map()
    return [tools[n] for n in names if n in tools]
