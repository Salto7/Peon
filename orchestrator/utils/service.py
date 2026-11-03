"""Process-wide default for catalog/runtime services."""

from __future__ import annotations

from agent_runtime.util import SharedBase


class SharedServiceBase(SharedBase):
    """Process-wide singleton helper (Django/orchestrator services).

    Prefer public **classmethods** that call ``cls.shared()`` internally
    (e.g. ``Session.current()``). Use ``Cls()`` + ``reset_shared()``
    for tests/DI; avoid module-level pass-through wrappers.

    Implementation lives in ``agent_runtime.util.SharedBase`` so runtime CLIs
    and orchestrator services share one singleton store.

    **Naming a subclass**: take the most specific noun that names the job it
    does — ``Router`` (routes), ``Registry`` (looks up), ``Runner`` (runs one
    thing), ``Catalog`` (holds a collection), ``Resolver``/``Provisioner``
    (turns a request into a result), ``Emitter`` (publishes), ``Backend``
    (one pluggable implementation of an external system), ``Proxy`` (forwards
    to another process), ``Sandbox`` (owns a sandboxed workspace), ``Linter``
    (validates one artifact), or a domain-specific pair like the Toolsmith
    ``*Engine`` authoring backends (``orchestrator/toolsmith/engine.py``).
    Fall back to the bare domain noun + ``Service`` (``DreamingService``,
    ``MemoryService``, ``LabService``) only when none of those fit — do not
    stack ``Service`` onto a class that already has a specific-enough suffix.
    """
