"""Register capability names for command-helper lookups.

Called capabilities (not “tools”) to avoid confusion with ``tools/catalog``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

_REGISTERED = False


class CapabilityGroup(StrEnum):
    SANDBOX = "sandbox"
    ENGAGEMENT = "engagement"


@dataclass(frozen=True)
class RegisteredCapability:
    """A capability's metadata — name/group/tags only.

    There is no handler to call: ``orchestrator.utils.commands`` reads
    ``group``/``tags`` off a looked-up name. The real tool implementation
    lives in ``orchestrator/crew/tools/adapters.py``.
    """

    group: CapabilityGroup
    tags: frozenset[str] = field(default_factory=frozenset)


class CapabilityRegistry:
    def __init__(self) -> None:
        self._by_name: dict[str, RegisteredCapability] = {}

    def register(
        self,
        group: CapabilityGroup,
        *,
        tags: Iterable[str] = (),
        name: str,
    ) -> None:
        key = name.strip()
        if not key:
            raise ValueError("Capability must have a name")
        if key in self._by_name:
            raise ValueError(f"Duplicate capability registration: {key!r}")
        self._by_name[key] = RegisteredCapability(
            group=group,
            tags=frozenset(str(t).strip() for t in tags if str(t).strip()),
        )

    def get(self, name: str) -> RegisteredCapability | None:
        return self._by_name.get(name)

    def names(
        self,
        *,
        groups: Iterable[CapabilityGroup] | None = None,
        include: Iterable[str] | None = None,
        exclude: Iterable[str] = (),
    ) -> list[str]:
        wanted = set(groups) if groups is not None else None
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
            out.append(name)
        return out


REGISTRY = CapabilityRegistry()


def ensure_registered() -> CapabilityRegistry:
    """Import capability names once so their module-load side effect populates REGISTRY."""
    global _REGISTERED
    if not _REGISTERED:
        # circular: capabilities.tools → CapabilityGroup from this module
        import orchestrator.capabilities.tools  # noqa: F401

        _REGISTERED = True
    return REGISTRY
