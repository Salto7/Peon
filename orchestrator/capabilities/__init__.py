"""Capability registry — named capabilities for command helpers."""

from orchestrator.capabilities.registry import (
    REGISTRY,
    CapabilityGroup,
    ensure_registered,
)

__all__ = [
    "CapabilityGroup",
    "REGISTRY",
    "ensure_registered",
]
