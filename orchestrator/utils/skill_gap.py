"""Skill-gap telemetry: a role tried a capability it has no binding for (Core 5.3).

No Django at module scope (same convention as ``orchestrator.utils.llm_usage``)
so this stays importable from the orchestrator layer without Django loaded.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def record_skill_gap(role_id: str, gap_kind: str, name: str) -> None:
    """Upsert one ``(role_id, gap_kind, name)`` row. Telemetry must never raise."""
    rid = (role_id or "").strip().lower()
    nm = (name or "").strip().lower()
    if not rid or not nm:
        return
    try:
        from django.db.models import F

        from peon.projects.models import SkillGapEvent, SkillGapKind

        kind = gap_kind if gap_kind in SkillGapKind.values else SkillGapKind.BINARY
        updated = (
            SkillGapEvent.objects.using("default")
            .filter(role_id=rid, gap_kind=kind, name=nm)
            .update(count=F("count") + 1)
        )
        if not updated:
            SkillGapEvent.objects.using("default").get_or_create(
                role_id=rid, gap_kind=kind, name=nm, defaults={"count": 1}
            )
    except Exception:  # noqa: BLE001 — telemetry must never break a real denial path
        logger.debug("record_skill_gap failed", exc_info=True)
