"""Project Exploration Graph: Observation / Intent store (Core 4.1).

Soft-referenced to the asset graph by ``asset_key``
(``peon.projects.targets.canonical_asset_key``), not a real FK —
``AssetGraph`` is one JSON blob per project.
"""

from __future__ import annotations

from peon.projects.db import ensure_project_database
from peon.projects.models import (
    Evidence,
    Finding,
    Intent,
    IntentStatus,
    Job,
    Objective,
    Observation,
    Project,
)


class ExplorationStore:
    """Record Observations/Intents tied to one asset-graph node."""

    @staticmethod
    def record_observation(
        project: Project,
        asset_key: str,
        text: str,
        *,
        job: Job | None = None,
        objective: Objective | None = None,
        finding: Finding | None = None,
        evidence: Evidence | None = None,
    ) -> Observation | None:
        key = (asset_key or "").strip()
        body = (text or "").strip()
        if not key or not body:
            return None
        return Observation.objects.using(ensure_project_database(project.pk)).create(
            project=project,
            asset_key=key,
            text=body,
            job=job,
            objective=objective,
            finding=finding,
            evidence=evidence,
        )

    @staticmethod
    def record_intent(
        project: Project,
        asset_key: str,
        text: str,
        *,
        job: Job | None = None,
        objective: Objective | None = None,
        status: str = IntentStatus.PLANNED,
    ) -> Intent | None:
        key = (asset_key or "").strip()
        body = (text or "").strip()
        if not key or not body:
            return None
        raw_status = (status or "").strip().lower()
        if raw_status not in {c.value for c in IntentStatus}:
            raw_status = IntentStatus.PLANNED
        return Intent.objects.using(ensure_project_database(project.pk)).create(
            project=project,
            asset_key=key,
            text=body,
            job=job,
            objective=objective,
            status=raw_status,
        )
