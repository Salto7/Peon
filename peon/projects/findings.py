"""Finding normalize / store / workspace-queue ingest (Django)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from peon.projects.models import Finding, FindingStatus, Job, Objective, Project
from peon.projects.targets import sanitize_label

# Ranking scale for reports/UI — not a domain taxonomy.
_SEVERITY_RANK = frozenset({"critical", "high", "medium", "low", "info"})
_SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")

# Operator triage: closed FindingStatus set (UI posts status=…; legacy action=… still maps).
TRIAGE_ACTIONS = {
    "verify": FindingStatus.CONFIRMED,
    "confirm": FindingStatus.CONFIRMED,
    "dismiss": FindingStatus.FALSE_POSITIVE,
    "accept": FindingStatus.ACCEPTED,
    "fixed": FindingStatus.FIXED,
    "reopen": FindingStatus.OPEN,
    "open": FindingStatus.OPEN,
    "confirmed": FindingStatus.CONFIRMED,
    "false_positive": FindingStatus.FALSE_POSITIVE,
    "accepted": FindingStatus.ACCEPTED,
}

# Excluded from client-facing report sections.
REPORT_HIDDEN_STATUSES = frozenset(
    {FindingStatus.FALSE_POSITIVE, FindingStatus.ACCEPTED}
)


class FindingNormalizer:
    """Coerce skill/queue dicts into Finding field values."""

    def normalize(self, record: dict[str, Any]) -> dict[str, Any] | None:
        title = str(record.get("title") or "").strip()
        if not title:
            return None
        kind = sanitize_label(
            str(record.get("kind") or ""),
            default="observation",
        )
        asset = sanitize_label(str(record.get("asset_type") or ""), default="")
        severity = sanitize_label(
            str(record.get("severity") or ""),
            default="info",
            max_len=20,
        )
        if severity not in _SEVERITY_RANK:
            severity = "info"
        port = record.get("port")
        try:
            port_i = int(port) if port is not None and str(port).strip() != "" else None
        except (TypeError, ValueError):
            port_i = None
        mitre = record.get("mitre") or record.get("mitre_techniques") or []
        if not isinstance(mitre, list):
            mitre = []
        meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        return {
            "title": title[:255],
            "kind": kind,
            "asset_type": asset,
            "severity": severity,
            "host": str(record.get("host") or "").strip()[:255],
            "service": str(record.get("service") or "").strip()[:128],
            "port": port_i,
            "protocol": str(record.get("protocol") or "").strip()[:16],
            "cve_id": str(record.get("cve_id") or "").strip()[:64],
            "cwe_id": str(record.get("cwe_id") or "").strip()[:32],
            "mitre_techniques": [str(x).strip() for x in mitre if str(x).strip()][:20],
            "description": str(record.get("description") or "").strip()[:8000],
            "evidence": str(record.get("evidence") or "").strip()[:16000],
            "evidence_path": str(record.get("evidence_path") or "").strip()[:1024],
            "remediation": str(record.get("remediation") or "").strip()[:4000],
            "metadata": meta,
        }


class FindingStore:
    """Django-backed finding CRUD for a project."""

    def __init__(self, normalizer: FindingNormalizer | None = None) -> None:
        self._normalizer = normalizer or FindingNormalizer()

    def record(
        self,
        project: Project,
        payload: dict[str, Any],
        *,
        job: Job | None = None,
        objective: Objective | None = None,
        dedupe: bool = True,
    ) -> Finding | None:
        row = self._normalizer.normalize(payload)
        if row is None:
            return None
        if dedupe:
            qs = Finding.objects.filter(
                project=project,
                kind=row["kind"],
                title=row["title"],
                host=row["host"],
            )
            qs = (
                qs.filter(port__isnull=True)
                if row["port"] is None
                else qs.filter(port=row["port"])
            )
            if row["evidence_path"]:
                qs = qs.filter(evidence_path=row["evidence_path"])
            existing = qs.first()
            if existing is not None:
                return existing
        finding = Finding.objects.create(
            project=project,
            job=job,
            objective=objective,
            seq=self._next_seq(project),
            **row,
        )
        self._queue_discovered_candidates(project, row)
        return finding

    @staticmethod
    def _queue_discovered_candidates(project: Project, row: dict[str, Any]) -> None:
        """Surface novel assets from finding text as RoE candidates (not authorized)."""
        roe = getattr(project, "roe", None)
        if roe is None:
            return
        from peon.projects.targets import (
            add_candidates,
            coerce_targets,
            extract_targets,
        )

        texts = [
            str(row.get("host") or ""),
            str(row.get("evidence") or ""),
            str(row.get("description") or ""),
            str(row.get("title") or ""),
        ]
        found = extract_targets(*(t for t in texts if t.strip()))
        if not found:
            return
        known = {
            t["value"].lower()
            for t in coerce_targets(roe.in_scope) + coerce_targets(roe.exclusions)
            if t.get("value")
        }
        novel = [t for t in found if t.get("value", "").lower() not in known]
        if novel:
            add_candidates(roe, novel)

    def set_status(self, finding: Finding, status: str) -> Finding | None:
        """Apply a FindingStatus value; returns None if status is invalid."""
        allowed = {c.value for c in FindingStatus}
        if status not in allowed:
            return None
        if finding.status == status:
            return finding
        finding.status = status
        finding.save(update_fields=["status", "updated_at"])
        return finding

    def triage(self, finding: Finding, action: str = "", *, status: str = "") -> Finding | None:
        """Apply a closed-set status (preferred) or legacy action verb."""
        raw = (status or action or "").strip().lower()
        target = TRIAGE_ACTIONS.get(raw)
        if target is None:
            allowed = {c.value for c in FindingStatus}
            if raw in allowed:
                target = raw
            else:
                return None
        return self.set_status(finding, target)

    def list_payload(
        self,
        project: Project | None,
        *,
        limit: int = 200,
        for_report: bool = False,
    ) -> list[dict]:
        if project is None:
            return []
        out: list[dict] = []
        qs = project.findings.order_by("seq", "created_at")
        if for_report:
            qs = qs.exclude(status__in=REPORT_HIDDEN_STATUSES)
        qs = qs[: max(1, min(limit, 500))]
        for f in qs:
            out.append(self._row_dict(f))
        return out

    def board(
        self,
        project: Project,
        *,
        status: str = "",
        severity: str = "",
        limit: int = 200,
    ) -> list[Finding]:
        """ORM rows for the project findings board (UI)."""
        qs = project.findings.all()
        status = (status or "").strip().lower()
        severity = (severity or "").strip().lower()
        if status and status != "all":
            qs = qs.filter(status=status)
        if severity and severity != "all":
            qs = qs.filter(severity=severity)
        # Severity-first for the board; seq as tiebreaker.
        order = {s: i for i, s in enumerate(_SEVERITY_ORDER)}
        rows = list(qs[: max(1, min(limit, 500))])
        rows.sort(
            key=lambda f: (order.get(f.severity, 99), f.seq, str(f.created_at))
        )
        return rows

    def board_payload(
        self,
        project: Project,
        *,
        status: str = "",
        severity: str = "",
        limit: int = 200,
    ) -> list[dict]:
        return [
            self._row_dict(f)
            for f in self.board(
                project, status=status, severity=severity, limit=limit
            )
        ]

    @staticmethod
    def _row_dict(f: Finding) -> dict:
        return {
            "id": str(f.id),
            "seq": f.seq,
            "title": f.title,
            "kind": f.kind,
            "asset_type": f.asset_type,
            "severity": f.severity,
            "status": f.status,
            "host": f.host,
            "service": f.service,
            "port": f.port,
            "protocol": f.protocol,
            "cve_id": f.cve_id,
            "cwe_id": f.cwe_id,
            "mitre_techniques": list(f.mitre_techniques or []),
            "description": f.description,
            "evidence": f.evidence,
            "evidence_path": f.evidence_path,
            "remediation": f.remediation,
        }

    @staticmethod
    def _next_seq(project: Project) -> int:
        last = (
            Finding.objects.filter(project=project)
            .order_by("-seq")
            .values_list("seq", flat=True)
            .first()
        )
        return int(last or 0) + 1


class FindingQueueIngestor:
    """Consume ``workspace/findings_queue.jsonl`` into Finding rows."""

    relative = Path("workspace") / "findings_queue.jsonl"
    archive_suffix = ".ingested"

    def __init__(self, store: FindingStore | None = None) -> None:
        self._store = store or FindingStore()

    def ingest(self, job: Job, workspace: Path) -> int:
        if job.project_id is None:
            return 0
        path = workspace / self.relative
        if not path.is_file():
            return 0
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return 0
        if not text.strip():
            return 0

        n = 0
        project = job.project
        objective = job.objective
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict) and self._store.record(
                project, raw, job=job, objective=objective
            ):
                n += 1

        archive = path.with_suffix(path.suffix + self.archive_suffix)
        try:
            existing = (
                archive.read_text(encoding="utf-8", errors="replace")
                if archive.is_file()
                else ""
            )
            archive.write_text(existing + text, encoding="utf-8")
            path.write_text("", encoding="utf-8")
        except OSError:
            pass
        return n


def ingest_workspace_findings(job: Job, workspace: Path) -> int:
    return FindingQueueIngestor().ingest(job, workspace)
