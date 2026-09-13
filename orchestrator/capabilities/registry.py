"""Register LangChain tools by name for ``allowed-tools`` filtering, referred here as capabilities instead of tools to avoid confusion with the tool catalog."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


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

    def tool_map(self) -> dict[str, Any]:
        return {name: entry.tool for name, entry in self._by_name.items()}

    def all_tools(self) -> list[Any]:
        return [entry.tool for entry in self._by_name.values()]


REGISTRY = CapabilityRegistry()


def capability(group: CapabilityGroup, *, tags: Iterable[str] = ()):
    """Register a LangChain ``@tool`` under ``group``."""

    def decorator(tool: Any) -> Any:
        return REGISTRY.register(group, tool, tags=tags)

    return decorator


def get_tools_for_names(names: set[str] | frozenset[str] | None) -> list[Any]:
    if names is None:
        return REGISTRY.all_tools()
    tool_map = REGISTRY.tool_map()
    return [tool_map[n] for n in names if n in tool_map]
