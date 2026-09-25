"""Project report synthesis: evidence → LLM or deterministic report.md."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from peon.projects.findings import FindingStore, ingest_workspace_findings
from peon.projects.models import Job, ObjectiveStatus
from peon.projects.targets import format_targets

_MAX_FILE = 12_000
_MAX_FILES = 40


def _read_capped(path: Path, limit: int = _MAX_FILE) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if len(text) > limit:
        return text[:limit].rstrip() + "\n\n…(truncated)…"
    return text


def _rel(ws: Path, path: Path) -> str:
    try:
        return path.relative_to(ws).as_posix()
    except ValueError:
        return path.name


@dataclass
class EvidenceBundle:
    """Snapshot of project evidence for the analyzer (no live queries after build)."""

    workspace: Path
    project_title: str = ""
    project_summary: str = ""
    in_scope: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    authorization: str = ""
    plan_markdown: str = ""
    objectives: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    phase_findings: list[tuple[str, str]] = field(default_factory=list)
    artifacts: list[tuple[str, str]] = field(default_factory=list)
    artifact_index: list[str] = field(default_factory=list)
    prior_jobs: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def from_workspace(
        cls,
        workspace: Path,
        *,
        project_title: str = "",
        project_summary: str = "",
        in_scope: list[str] | None = None,
        exclusions: list[str] | None = None,
        authorization: str = "",
        objectives: list[dict[str, Any]] | None = None,
        findings: list[dict[str, Any]] | None = None,
        prior_jobs: list[dict[str, str]] | None = None,
    ) -> EvidenceBundle:
        """Filesystem-only bundle (skill standalone / tests)."""
        return cls(
            workspace=workspace,
            project_title=project_title,
            project_summary=project_summary,
            in_scope=list(in_scope or []),
            exclusions=list(exclusions or []),
            authorization=authorization,
            plan_markdown=_read_capped(workspace / "plans" / "latest.md"),
            objectives=list(objectives or []),
            findings=list(findings or []),
            phase_findings=_collect_phase_findings(workspace),
            artifacts=_collect_workspace_artifacts(workspace),
            artifact_index=_evidence_index(workspace),
            prior_jobs=list(prior_jobs or []),
        )

    @classmethod
    def from_job(cls, job: Job, workspace: Path) -> EvidenceBundle:
        project = job.project
        title = (project.title if project else job.title) or ""
        summary = (project.summary if project else "") or ""
        in_scope: list[str] = []
        exclusions: list[str] = []
        authorization = ""
        objectives: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        prior_jobs: list[dict[str, str]] = []

        if project is not None:
            roe = getattr(project, "roe", None)
            if roe is not None:
                in_scope = format_targets(roe.in_scope)
                exclusions = format_targets(roe.exclusions)
                authorization = (roe.authorization_note or "").strip()
            for obj in project.objectives.order_by("seq", "created_at"):
                objectives.append(
                    {
                        "seq": obj.seq,
                        "title": obj.title,
                        "phase": obj.phase,
                        "status": obj.status,
                        "description": obj.description,
                        "acceptance_criteria": obj.acceptance_criteria,
                        "skill_suggestion": obj.skill_suggestion,
                        "blocked_reason": obj.blocked_reason,
                    }
                )
            findings = FindingStore().list_payload(project, for_report=True)
            for j in project.jobs.order_by("created_at"):
                if j.pk == job.pk:
                    continue
                prior_jobs.append(
                    {
                        "id": str(j.id),
                        "title": j.title,
                        "status": j.status,
                        "skills": ", ".join(
                            str(s) for s in (j.skill_names or []) if str(s).strip()
                        ),
                    }
                )

        return cls.from_workspace(
            workspace,
            project_title=title,
            project_summary=summary,
            in_scope=in_scope,
            exclusions=exclusions,
            authorization=authorization,
            objectives=objectives,
            findings=findings,
            prior_jobs=prior_jobs,
        )

    def coverage_summary(self) -> dict[str, list[str]]:
        buckets = {
            "completed": [],
            "blocked": [],
            "pending": [],
            "cancelled": [],
            "other": [],
        }
        for obj in self.objectives:
            status = str(obj.get("status") or "")
            label = f"OBJ-{obj.get('seq')}: {obj.get('title')} [{obj.get('phase')}]"
            if status == ObjectiveStatus.COMPLETED:
                buckets["completed"].append(label)
            elif status == ObjectiveStatus.BLOCKED:
                reason = obj.get("blocked_reason") or ""
                buckets["blocked"].append(f"{label} — {reason}" if reason else label)
            elif status == ObjectiveStatus.PENDING:
                buckets["pending"].append(label)
            elif status == ObjectiveStatus.CANCELLED:
                buckets["cancelled"].append(label)
            else:
                buckets["other"].append(f"{label} ({status})")
        return buckets

    def to_prompt_context(self, *, max_chars: int = 48_000) -> str:
        """Compact text block for the LLM synthesizer."""
        cov = self.coverage_summary()
        parts = [
            f"# Project: {self.project_title}",
            f"Summary: {self.project_summary or '(none)'}",
            "",
            "## Rules of Engagement",
            f"In-scope: {', '.join(self.in_scope) or '(empty)'}",
            f"Exclusions: {', '.join(self.exclusions) or '(none)'}",
            f"Authorization: {self.authorization or '(none)'}",
            "",
            "## Objective coverage",
            json.dumps(cov, indent=2),
            "",
            "## Structured findings (DB)",
            json.dumps(self.findings[:80], indent=2)[:16_000],
            "",
            "## Plan",
            self.plan_markdown[:8_000] or "(no plans/latest.md)",
            "",
            "## Artifact index",
            "\n".join(self.artifact_index[:80]) or "(none)",
            "",
            "## Prior jobs",
            json.dumps(self.prior_jobs[:40], indent=2),
        ]
        for rel, body in self.phase_findings[:12]:
            parts.extend(["", f"## Phase file `{rel}`", body[:4_000]])
        for rel, body in self.artifacts[:12]:
            parts.extend(["", f"## Artifact `{rel}`", body[:4_000]])
        text = "\n".join(parts)
        if len(text) > max_chars:
            return text[:max_chars].rstrip() + "\n\n…(context truncated)…"
        return text


def _collect_phase_findings(ws: Path) -> list[tuple[str, str]]:
    findings = ws / "findings"
    if not findings.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for path in sorted(findings.glob("*.md")):
        if path.name.lower() == "report.md":
            continue
        body = _read_capped(path)
        if body:
            out.append((_rel(ws, path), body))
        if len(out) >= _MAX_FILES:
            break
    return out


def _collect_workspace_artifacts(ws: Path) -> list[tuple[str, str]]:
    root = ws / "workspace"
    if not root.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.endswith(".ingested") or path.name == "findings_queue.jsonl":
            continue
        if path.suffix.lower() not in {".md", ".txt", ".json", ".csv", ".out"}:
            continue
        body = _read_capped(path)
        if body:
            out.append((_rel(ws, path), body))
        if len(out) >= _MAX_FILES:
            break
    return out


def _evidence_index(ws: Path) -> list[str]:
    lines: list[str] = []
    for pattern in ("**/*.md", "**/*.json", "**/*.txt", "**/*.out", "**/*.csv"):
        for path in sorted(ws.glob(pattern)):
            if path.name == "report.md" or path.name.endswith(".ingested"):
                continue
            lines.append(f"- `{_rel(ws, path)}`")
    seen: set[str] = set()
    uniq: list[str] = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            uniq.append(line)
    return uniq[:120]


ANALYZER_SYSTEM = """You are the project ANALYZER for an authorized engagement orchestrator.

Write a FULLY SELF-CONTAINED markdown report. Do not collect new evidence or invent
results. Synthesize only what is already in the evidence bundle.

Subjects and findings are asset-agnostic (network assets, files, malware/samples,
source, packages, identities, cloud resources, services, …) — not limited to
web/network targets. Finding kind/asset_type labels are free-form.

Required sections (use these exact ## headings):
1. ## Executive summary
2. ## Scope / Rules of Engagement
3. ## Project-plan coverage
4. ## Inventories
5. ## Findings by severity
6. ## Gaps / next steps

Rules:
- Embed inventories and finding details inline (tables or bullets). Match columns
  to the evidence (do not force a fixed schema).
- Do NOT tell the reader to open other files (no “see findings/foo.md”).
- Group findings by severity (critical → info). Cite evidence inline.
- Cover completed / blocked / pending / cancelled objectives explicitly.
- If evidence is thin, say so honestly under Gaps — do not pad with speculation.
- Return ONLY the markdown report body (start with # title). No preamble.
"""


class InventoryTableBuilder:
    """Scan ``workspace/**/*.json`` and render markdown tables from list-of-objects."""

    max_files = 24
    max_rows = 80
    max_cols = 8
    max_cell = 100
    max_depth = 4
    skip_names = frozenset({"findings_queue.jsonl", "analysis_context.json"})
    skip_suffixes = (".ingested",)

    def render(self, workspace: Path) -> str:
        sections: list[str] = []
        for path in self._iter_json_files(workspace):
            data = self._load_json(path)
            if data is None:
                continue
            records = self._as_rows(data)[: self.max_rows]
            if not records:
                continue
            cols = self._infer_columns(records)
            if not cols:
                continue
            table_rows = [[self._cell(item.get(c)) for c in cols] for item in records]
            keep = [i for i in range(len(cols)) if any(r[i] for r in table_rows)]
            if not keep:
                continue
            cols = [cols[i] for i in keep]
            table_rows = [[r[i] for i in keep] for r in table_rows]
            try:
                rel = path.relative_to(workspace).as_posix()
            except ValueError:
                rel = path.name
            sections.append(f"### `{rel}`")
            sections.append(self._md_table(cols, table_rows))
        return "\n\n".join(sections) if sections else ""

    def _iter_json_files(self, workspace: Path) -> list[Path]:
        root = workspace / "workspace"
        if not root.is_dir():
            return []
        out: list[Path] = []
        for path in sorted(root.rglob("*.json")):
            if not path.is_file():
                continue
            if path.name in self.skip_names or path.name.endswith(self.skip_suffixes):
                continue
            try:
                depth = len(path.relative_to(root).parts)
            except ValueError:
                continue
            if depth > self.max_depth:
                continue
            out.append(path)
            if len(out) >= self.max_files:
                break
        return out

    @staticmethod
    def _load_json(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _list_of_objects(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not value:
            return []
        rows = [x for x in value if isinstance(x, dict)]
        if not rows:
            return []
        non_null = sum(1 for x in value if x is not None)
        return rows if len(rows) * 2 >= non_null else []

    def _as_rows(self, data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return self._list_of_objects(data)
        if not isinstance(data, dict):
            return []
        best: list[dict[str, Any]] = []
        for val in data.values():
            rows = self._list_of_objects(val)
            if len(rows) > len(best):
                best = rows
        if best:
            return best
        if data and all(not isinstance(v, (list, dict)) for v in data.values()):
            return [data]
        return []

    def _cell(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (list, dict)):
            try:
                text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError):
                text = str(value)
        else:
            text = str(value)
        text = text.replace("\n", " ").strip()
        if len(text) > self.max_cell:
            text = text[: self.max_cell - 1].rstrip() + "…"
        return text.replace("|", "\\|")

    def _infer_columns(self, rows: list[dict[str, Any]]) -> list[str]:
        first_index: dict[str, int] = {}
        counts: Counter[str] = Counter()
        for row in rows:
            for key in row:
                name = str(key)
                if name.startswith("_"):
                    continue
                if name not in first_index:
                    first_index[name] = len(first_index)
                counts[name] += 1
        if not counts:
            return []
        ordered = sorted(counts.keys(), key=lambda k: (-counts[k], first_index[k], k))
        return ordered[: self.max_cols]

    @staticmethod
    def _md_table(headers: list[str], rows: list[list[str]]) -> str:
        if not headers or not rows:
            return "_None._"
        head = "| " + " | ".join(headers) + " |"
        sep = "| " + " | ".join("---" for _ in headers) + " |"
        body = ["| " + " | ".join(row) + " |" for row in rows]
        return "\n".join([head, sep, *body])


_SEV_ORDER = ("critical", "high", "medium", "low", "info")


class DeterministicReportBuilder:
    def __init__(self) -> None:
        self._inventories = InventoryTableBuilder()

    def build(self, bundle: EvidenceBundle) -> Path:
        ws = bundle.workspace
        findings_dir = ws / "findings"
        findings_dir.mkdir(parents=True, exist_ok=True)
        report = findings_dir / "report.md"
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        cov = bundle.coverage_summary()

        parts: list[str] = [
            f"# Project report — {bundle.project_title or 'untitled'}",
            "",
            f"Generated: {stamp}",
            "Synthesizer: deterministic (peon.projects.analysis)",
            "",
            "## Executive summary",
            "",
            self._summary(bundle),
            "",
            "## Scope / Rules of Engagement",
            "",
            f"- **In-scope:** {', '.join(bundle.in_scope) or '(empty)'}",
            f"- **Exclusions:** {', '.join(bundle.exclusions) or '(none)'}",
        ]
        if bundle.authorization:
            parts.append(f"- **Authorization:** {bundle.authorization}")
        if bundle.project_summary:
            parts.extend(["", bundle.project_summary])

        parts.extend(["", "## Project-plan coverage", ""])
        for key in ("completed", "blocked", "pending", "cancelled", "other"):
            items = cov.get(key) or []
            parts.append(f"### {key.title()} ({len(items)})")
            if items:
                parts.extend(f"- {x}" for x in items)
            else:
                parts.append("_None._")
            parts.append("")

        if bundle.plan_markdown:
            parts.extend(["### Plan text", "", bundle.plan_markdown, ""])

        tables = self._inventories.render(ws)
        parts.extend(
            [
                "## Inventories",
                "",
                tables
                or "_No structured JSON inventory handoffs found under `workspace/`._",
                "",
                "## Evidence index",
                "",
                "\n".join(bundle.artifact_index)
                if bundle.artifact_index
                else "_No workspace evidence files found._",
                "",
                "## Findings by severity",
                "",
            ]
        )
        parts.extend(self._findings_section(bundle))

        if bundle.phase_findings:
            parts.extend(["## Phase deliverables", ""])
            for rel, body in bundle.phase_findings:
                parts.extend([f"### `{rel}`", "", body, ""])

        parts.extend(
            [
                "## Gaps / next steps",
                "",
                "- Confirm blocked/pending objectives and re-run only those skills.",
                "- Queue **engagement** findings via ``record_finding`` "
                "(discoveries about any subject class with evidence — not "
                "job/objective/agent status).",
                "- Re-run analyzer after additional evidence lands.",
                "",
            ]
        )
        report.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
        return report

    @staticmethod
    def _summary(bundle: EvidenceBundle) -> str:
        if bundle.findings or bundle.phase_findings or bundle.artifacts:
            return (
                f"Evidence-only synthesis for **{bundle.project_title or 'project'}**. "
                f"Structured findings: {len(bundle.findings)}. "
                f"Phase markdown files: {len(bundle.phase_findings)}. "
                "No new collection; no invented discoveries."
            )
        return (
            "Little curated evidence was present. Only the project plan / Rules of Engagement "
            "and any indexed files were available."
        )

    @staticmethod
    def _findings_section(bundle: EvidenceBundle) -> list[str]:
        if not bundle.findings:
            return ["_No structured Finding rows recorded._", ""]
        by_sev: dict[str, list[dict]] = {s: [] for s in _SEV_ORDER}
        for row in bundle.findings:
            sev = str(row.get("severity") or "info").lower()
            by_sev.setdefault(sev, []).append(row)
        parts: list[str] = []
        for sev in _SEV_ORDER:
            rows = by_sev.get(sev) or []
            if not rows:
                continue
            parts.append(f"### {sev.title()} ({len(rows)})")
            for row in rows:
                loc = ""
                host = str(row.get("host") or "").strip()
                service = str(row.get("service") or "").strip()
                port = row.get("port")
                asset_type = str(row.get("asset_type") or "").strip()
                if host and port:
                    loc = f"{host}:{port}"
                elif host:
                    loc = host
                elif service:
                    loc = service
                bits = [b for b in (asset_type, loc) if b]
                asset = f" ({', '.join(bits)})" if bits else ""
                parts.append(
                    f"- **FIND-{row.get('seq')}** [{row.get('kind')}] "
                    f"{row.get('title')}{asset}"
                )
                if row.get("evidence"):
                    parts.append(f"  - Evidence: {row['evidence'][:500]}")
                if row.get("evidence_path"):
                    parts.append(f"  - Path: `{row['evidence_path']}`")
            parts.append("")
        return parts


class LlmReportBuilder:
    """LLM synthesizer; returns None when unavailable or output fails quality checks."""

    min_chars = 80

    def build(self, bundle: EvidenceBundle) -> Path | None:
        try:
            from orchestrator.utils.llm import chat_text, llm_config
        except Exception:
            return None
        if not (llm_config().api_key or "").strip():
            return None
        human = (
            "Synthesize the final project report from this evidence pack.\n\n"
            + bundle.to_prompt_context()
        )
        try:
            text = chat_text(ANALYZER_SYSTEM, human).strip()
        except Exception:
            return None
        if len(text) < self.min_chars:
            return None
        if "## Executive summary" not in text and "## Findings" not in text:
            return None
        if not text.lstrip().startswith("#"):
            title = bundle.project_title or "Project report"
            text = f"# Project report — {title}\n\n{text}"
        stamp = "Synthesizer: llm (peon.projects.analysis)\n\n"
        if "Synthesizer:" not in text[:400]:
            lines = text.splitlines()
            if lines:
                text = lines[0] + "\n\n" + stamp + "\n".join(lines[1:])
        report = bundle.workspace / "findings" / "report.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(text.rstrip() + "\n", encoding="utf-8")
        return report


class ReportSynthesizer:
    """Chain of report builders; first non-None result wins."""

    def __init__(self, builders: list | None = None) -> None:
        self.builders = builders or [LlmReportBuilder(), DeterministicReportBuilder()]

    def run(self, job: Job, workspace: Path) -> Path:
        ingest_workspace_findings(job, workspace)
        bundle = EvidenceBundle.from_job(job, workspace)
        (workspace / "findings").mkdir(parents=True, exist_ok=True)
        path: Path | None = None
        for builder in self.builders:
            path = builder.build(bundle)
            if path is not None:
                break
        if path is None:
            path = DeterministicReportBuilder().build(bundle)
        self._complete_reporting_objectives(job)
        return path

    @staticmethod
    def _complete_reporting_objectives(job: Job) -> None:
        if job.project_id is None:
            return
        for obj in job.project.objectives.filter(skill_suggestion="analyzer").exclude(
            status=ObjectiveStatus.CANCELLED
        ):
            obj.status = ObjectiveStatus.COMPLETED
            obj.blocked_reason = ""
            obj.save(update_fields=["status", "blocked_reason", "updated_at"])


def synthesize_report_for_job(job: Job, workspace: Path) -> Path:
    return ReportSynthesizer().run(job, workspace)

