"""Process-wide default for catalog/runtime services."""

from __future__ import annotations

from typing import Any, ClassVar, Self


class SharedService:
    """Process-wide singleton helper.

    Prefer public **classmethods** that call ``cls.shared()`` internally
    (e.g. ``SandboxSession.current()``). Use ``Cls()`` + ``reset_shared()``
    for tests/DI; avoid module-level pass-through wrappers.
    """

    _shared: ClassVar[dict[type, Any]] = {}

    @classmethod
    def shared(cls) -> Self:
        if cls not in cls._shared:
            cls._shared[cls] = cls()
        return cls._shared[cls]

    @classmethod
    def reset_shared(cls) -> None:
        cls._shared.pop(cls, None)
