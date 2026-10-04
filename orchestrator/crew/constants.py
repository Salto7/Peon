"""Engagement bookend helpers — ids come from ROLE.yaml hierarchy."""

from __future__ import annotations


def engagement_start_role() -> str:
    from orchestrator.crew.roles.hierarchy import engagement_bookends

    start, _ = engagement_bookends()
    return start


def engagement_end_role() -> str:
    from orchestrator.crew.roles.hierarchy import engagement_bookends

    _, end = engagement_bookends()
    return end
