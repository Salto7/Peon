"""Finding normalize / store / workspace-queue ingest (Django)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from peon.projects.models import Finding, FindingStatus, Job, Objective, Project
from peon.projects.targets import sanitize_label

# Ranking scale for reports/UI — not a domain taxonomy.
_SEVERITY_RANK = frozenset({"critical", "high", "medium", "low", "info"})
_SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")

# Excluded from client-facing report sections.
REPORT_HIDDEN_STATUSES = frozenset(
    {FindingStatus.FALSE_POSITIVE, FindingStatus.ACCEPTED}
)

# Kinds that mean run/lifecycle noise, not engagement discoveries.
_STATUS_KINDS = frozenset(
    {
        "status",
        "progress",
        "lifecycle",
        "agent",
        "agent_status",
        "job_status",
        "objective_status",
        "run",
        "heartbeat",
    }
)

# Titles that are clearly skill/job/objective status (not findings).
_STATUS_TITLE_RE = re.compile(
    r"(?ix)"
    r"("
    r"^\S+\s*/\s*\S+\s+completed\s*$"  # "network-scanner / nmap completed"
    r"|^(skill|job|agent|objective|obj-\d+|scan|task)\b.*\b"
    r"(completed|finished|started|running|failed|blocked|cancelled)\b"
    r"|\b(objective|job|agent)\s+(status|progress|update)\b"
    r"|^(updated|marked)\s+obj-\d+"
    r")"
)


def is_status_noise(title: str, kind: str = "") -> bool:
    """True when the payload looks like agent/objective/run status, not a finding."""
    k = sanitize_label(kind or "")
    if k in _STATUS_KINDS:
        return True
    t = (title or "").strip()
    if not t:
        return True
    return bool(_STATUS_TITLE_RE.search(t))


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
        if is_status_noise(title, kind):
            return None
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
        """Surface novel assets from structured finding fields as RoE candidates."""
        roe = getattr(project, "roe", None)
        if roe is None:
            return
        from peon.projects.targets import (
            add_candidates,
            coerce_targets,
            discovery_assets_from_finding,
        )

        found = discovery_assets_from_finding(row)
        if not found:
            return
        known = {
            t["value"].lower()
            for t in (
                coerce_targets(roe.seed)
                + coerce_targets(roe.in_scope)
                + coerce_targets(roe.exclusions)
                + coerce_targets(roe.candidates)
            )
            if t.get("value")
        }
        novel = [t for t in found if t.get("value", "").lower() not in known]
        if novel:
            add_candidates(roe, novel)

    def triage(self, finding: Finding, action: str = "", *, status: str = "") -> Finding | None:
        """Apply a FindingStatus value (``status`` preferred; ``action`` alias)."""
        raw = (status or action or "").strip().lower()
        allowed = {c.value for c in FindingStatus}
        if raw not in allowed:
            return None
        if finding.status == raw:
            return finding
        finding.status = raw
        finding.save(update_fields=["status", "updated_at"])
        return finding

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

    @staticmethod
    def board_counts(project: Project) -> dict:
        """Aggregate finding counts for the triage board header."""
        from django.db.models import Count, Q

        return project.findings.aggregate(
            all=Count("id"),
            open=Count("id", filter=Q(status=FindingStatus.OPEN)),
            confirmed=Count("id", filter=Q(status=FindingStatus.CONFIRMED)),
        )

    @classmethod
    def board_query(
        cls,
        project: Project,
        request=None,
        *,
        status: str | None = None,
        severity: str | None = None,
        default_status: str = "open",
    ) -> dict:
        """Findings board payload + filters for detail page / JSON poll."""
        if request is not None:
            status_f = (request.GET.get("finding_status") or default_status).strip().lower()
            severity_f = (request.GET.get("finding_severity") or "all").strip().lower()
        else:
            status_f = (status or default_status).strip().lower()
            severity_f = (severity or "all").strip().lower()
        store = cls()
        rows = store.board(project, status=status_f, severity=severity_f)
        return {
            "status": status_f,
            "severity": severity_f,
            "findings": rows,
            "payload": [store._row_dict(f) for f in rows],
            "counts": cls.board_counts(project),
        }

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
