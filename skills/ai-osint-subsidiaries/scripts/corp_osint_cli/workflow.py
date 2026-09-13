"""Ordered, resumable corporate discovery -> domain enumeration workflow."""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from corp_osint_cli.pipeline import run_pipeline
from corp_osint_cli.schema import (
    compact_rows,
    export_seeds,
    full_envelope,
    normalize_findings,
)

WORKFLOW_FORMAT = "corporate-recon-workflow/v1"
STATE_FORMAT = "corporate-recon-state/v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


@dataclass
class CorporateReconState:
    """Serializable workflow state suitable for file or external checkpoints."""

    format: str = STATE_FORMAT
    input_fingerprint: str = ""
    company: str = ""
    ticker: str = ""
    cik: str = ""
    qid: str = ""
    workspace: str = ""
    status: str = "pending"
    current_step: str = ""
    completed_steps: list[str] = field(default_factory=list)
    attempts: dict[str, int] = field(default_factory=dict)
    step1: dict[str, Any] = field(default_factory=dict)
    step2: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CorporateReconState":
        if not isinstance(raw, dict) or raw.get("format") != STATE_FORMAT:
            raise ValueError("unsupported corporate recon checkpoint")
        field_names = cls.__dataclass_fields__
        return cls(**{key: value for key, value in raw.items() if key in field_names})


Runner = Callable[[CorporateReconState, Path], tuple[int, dict[str, Any]]]
CheckpointCallback = Callable[[dict[str, Any]], None]


class CorporateReconWorkflow:
    """Run corporate discovery before, and only before, domain enumeration.

    Runners are injectable so tests and alternate runtimes do not need network or
    Docker. Every transition is persisted locally and can also be sent to a
    durable checkpoint callback.
    """

    def __init__(
        self,
        workspace: str | Path = "workspace",
        *,
        discovery_runner: Runner | None = None,
        subsidiary_runner: Runner | None = None,
        domain_runner: Runner | None = None,
        checkpoint_callback: CheckpointCallback | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.discovery_runner = discovery_runner or self._default_discovery_runner
        self.subsidiary_runner = subsidiary_runner
        self.domain_runner = domain_runner or self._default_domain_runner
        self.checkpoint_callback = checkpoint_callback
        self.raw_dir = self.workspace / "raw" / "corporate-recon"
        self.state_path = self.raw_dir / "state.json"
        self.canonical_path = self.workspace / "corp-entities.json"
        self.result_path = self.workspace / "corporate-recon-result.json"

    def run(
        self,
        *,
        company: str,
        ticker: str = "",
        cik: str = "",
        qid: str = "",
        resume: bool = True,
    ) -> CorporateReconState:
        inputs = self._normalize_inputs(
            company=company, ticker=ticker, cik=cik, qid=qid
        )
        fingerprint = _json_hash(inputs)
        state = self._load_state(fingerprint) if resume else None
        if state is None:
            state = CorporateReconState(
                input_fingerprint=fingerprint,
                workspace=str(self.workspace),
                **inputs,
            )
            self._checkpoint(state)

        if "corporate_discovery" in state.completed_steps:
            try:
                self._validate_published_handoff(state)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                state.completed_steps = [
                    step
                    for step in state.completed_steps
                    if step not in {"corporate_discovery", "domain_enumeration"}
                ]
                state.step1 = {}
                state.step2 = {}
                state.result = {}
                state.status = "pending"
                state.error = {
                    "step": "corporate_discovery",
                    "type": type(exc).__name__,
                    "message": f"published handoff is no longer valid: {exc}",
                    "at": _utc_now(),
                }
                self._checkpoint(state)

        if "corporate_discovery" not in state.completed_steps:
            if not self._run_step1(state):
                return state

        # This guard is intentionally repeated at the boundary. A runner cannot
        # advance the workflow by merely returning a successful status.
        try:
            self._validate_published_handoff(state)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return self._fail(state, "corporate_discovery", exc)

        if (
            state.status == "completed"
            and "domain_enumeration" in state.completed_steps
            and self.result_path.is_file()
            and state.result.get("sha256") == _file_hash(self.result_path)
        ):
            return state

        if "domain_enumeration" not in state.completed_steps:
            if not self._run_step2(state):
                return state

        if not state.result or not self.result_path.is_file():
            self._write_final_result(state)
        state.current_step = ""
        state.status = "completed"
        state.error = None
        self._checkpoint(state)
        return state

    @staticmethod
    def _normalize_inputs(
        *, company: str, ticker: str, cik: str, qid: str
    ) -> dict[str, str]:
        normalized_company = " ".join((company or "").split())
        if not normalized_company:
            raise ValueError("company name is required")
        digits = "".join(ch for ch in str(cik or "") if ch.isdigit())
        normalized_qid = (qid or "").strip().upper()
        if normalized_qid and (
            not normalized_qid.startswith("Q") or not normalized_qid[1:].isdigit()
        ):
            raise ValueError("qid must have the form Q123")
        return {
            "company": normalized_company,
            "ticker": (ticker or "").strip().upper(),
            "cik": digits.zfill(10) if digits else "",
            "qid": normalized_qid,
        }

    def _load_state(self, fingerprint: str) -> CorporateReconState | None:
        if not self.state_path.is_file():
            return None
        try:
            state = CorporateReconState.from_dict(
                json.loads(self.state_path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        return state if state.input_fingerprint == fingerprint else None

    def _checkpoint(self, state: CorporateReconState) -> None:
        state.updated_at = _utc_now()
        _atomic_json(self.state_path, state.to_dict())
        if self.checkpoint_callback is not None:
            self.checkpoint_callback(state.to_dict())

    def _run_step1(self, state: CorporateReconState) -> bool:
        step = "corporate_discovery"
        state.current_step = step
        state.status = "running"
        state.error = None
        state.attempts[step] = int(state.attempts.get(step, 0)) + 1
        self._checkpoint(state)

        stage = self.raw_dir / "runs" / state.input_fingerprint
        try:
            code, primary = self.discovery_runner(state, stage / "corp-osint")
            if code != 0 or str(primary.get("status") or "") != "ok":
                raise RuntimeError(
                    f"corp-osint discovery failed (exit {code}): "
                    f"{primary.get('error') or primary.get('status') or 'unknown error'}"
                )
            rows = self._load_runner_rows(primary, stage / "corp-osint")
            sources: dict[str, Any] = {"corp_osint": primary}
            warnings: list[dict[str, Any]] = []

            if self.subsidiary_runner is not None:
                aux_code, auxiliary = self.subsidiary_runner(
                    state, stage / "ai-osint-subsidiaries"
                )
                sources["ai_osint_subsidiaries"] = auxiliary
                if aux_code == 0 and str(auxiliary.get("status") or "") == "ok":
                    rows.extend(
                        self._load_runner_rows(
                            auxiliary, stage / "ai-osint-subsidiaries"
                        )
                    )
                else:
                    warnings.append(
                        {
                            "capability": "ai-osint-subsidiaries",
                            "exit_code": aux_code,
                            "status": auxiliary.get("status") or "error",
                            "message": auxiliary.get("error")
                            or auxiliary.get("hint")
                            or "subsidiary enrichment unavailable",
                        }
                    )

            normalized = normalize_findings(rows, allow_empty=False)
            compact = compact_rows(normalized)
            domain_count = len(
                {row["domain"] for row in compact if row.get("domain")}
            )
            if domain_count == 0:
                raise ValueError(
                    "corporate discovery produced no valid associated domains"
                )

            _atomic_json(self.canonical_path, compact)
            canonical_hash = _file_hash(self.canonical_path)
            provenance = full_envelope(
                normalized,
                company=state.company,
                qid=state.qid,
                extra={
                    "workflow": WORKFLOW_FORMAT,
                    "input_fingerprint": state.input_fingerprint,
                    "ticker": state.ticker,
                    "cik": state.cik,
                    "sources": sources,
                    "warnings": warnings,
                    "canonical_sha256": canonical_hash,
                },
            )
            provenance_path = self.raw_dir / "entities.full.json"
            _atomic_json(provenance_path, provenance)
            counts = export_seeds(
                normalized,
                seeds_path=self.workspace / "corp-seeds.txt",
                domains_path=self.workspace / "corp-domains.txt",
            )
            state.step1 = {
                "status": "completed",
                "completed_at": _utc_now(),
                "attempt": state.attempts[step],
                "canonical_path": str(self.canonical_path),
                "canonical_sha256": canonical_hash,
                "provenance_path": str(provenance_path),
                "entity_count": len(compact),
                "domain_count": domain_count,
                "warnings": warnings,
                **counts,
            }
            state.completed_steps = [
                item for item in state.completed_steps if item != step
            ] + [step]
            state.error = None
            self._checkpoint(state)
            return True
        except Exception as exc:
            self._fail(state, step, exc)
            return False

    def _load_runner_rows(
        self, summary: dict[str, Any], default_dir: Path
    ) -> list[dict[str, Any]]:
        candidates = [
            summary.get("raw_path"),
            summary.get("entities_path"),
            default_dir / "raw" / "corp-osint" / "entities.full.json",
            default_dir
            / "raw"
            / "ai-osint-subsidiaries"
            / "entities.full.json",
            default_dir / "corp-entities.json",
        ]
        for candidate in candidates:
            if not candidate:
                continue
            path = Path(candidate)
            if not path.is_file():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            return normalize_findings(payload, allow_empty=False)
        findings = summary.get("findings")
        if findings is not None:
            return normalize_findings(findings, allow_empty=False)
        raise ValueError("discovery runner did not provide a readable findings artifact")

    def _validate_published_handoff(
        self, state: CorporateReconState
    ) -> list[dict[str, str]]:
        if not self.canonical_path.is_file():
            raise ValueError(f"missing canonical handoff {self.canonical_path}")
        expected_hash = str(state.step1.get("canonical_sha256") or "")
        if not expected_hash or _file_hash(self.canonical_path) != expected_hash:
            raise ValueError("canonical handoff hash does not match completed step 1")
        payload = json.loads(self.canonical_path.read_text(encoding="utf-8"))
        rows = normalize_findings(payload, allow_empty=False)
        if not any(row.get("domain") for row in rows):
            raise ValueError("canonical handoff contains no valid domains")
        return rows

    def _run_step2(self, state: CorporateReconState) -> bool:
        step = "domain_enumeration"
        state.current_step = step
        state.status = "running"
        state.error = None
        state.attempts[step] = int(state.attempts.get(step, 0)) + 1
        self._checkpoint(state)
        try:
            code, summary = self.domain_runner(state, self.workspace)
            if code != 0:
                raise RuntimeError(
                    f"domain-enum failed (exit {code}): "
                    f"{summary.get('error') or summary.get('status') or 'unknown error'}"
                )
            state.step2 = {
                "status": "completed",
                "completed_at": _utc_now(),
                "attempt": state.attempts[step],
                "input_path": str(self.canonical_path),
                "input_sha256": state.step1["canonical_sha256"],
                "summary": summary,
            }
            state.completed_steps = [
                item for item in state.completed_steps if item != step
            ] + [step]
            state.error = None
            self._checkpoint(state)
            return True
        except Exception as exc:
            self._fail(state, step, exc)
            return False

    def _write_final_result(self, state: CorporateReconState) -> None:
        rows = self._validate_published_handoff(state)
        domains = sorted({row["domain"] for row in rows if row.get("domain")})
        subdomains_path = self.workspace / "subdomains.json"
        subdomains: list[str] = []
        if subdomains_path.is_file():
            try:
                payload = json.loads(subdomains_path.read_text(encoding="utf-8"))
                values = payload.get("subdomains", []) if isinstance(payload, dict) else payload
                if isinstance(values, list):
                    subdomains = sorted(
                        {
                            str(value).strip().lower().rstrip(".")
                            for value in values
                            if str(value).strip()
                        }
                    )
            except (OSError, json.JSONDecodeError):
                subdomains = []
        result = {
            "format": WORKFLOW_FORMAT,
            "status": "completed",
            "generated_at": _utc_now(),
            "input": {
                "company": state.company,
                "ticker": state.ticker,
                "cik": state.cik,
                "qid": state.qid,
                "fingerprint": state.input_fingerprint,
            },
            "steps": {
                "corporate_discovery": state.step1,
                "domain_enumeration": state.step2,
            },
            "canonical": {
                "path": str(self.canonical_path),
                "sha256": state.step1["canonical_sha256"],
                "entity_count": len(rows),
                "domain_count": len(domains),
            },
            "domains": domains,
            "subdomains": subdomains,
            "subdomain_count": len(subdomains),
        }
        _atomic_json(self.result_path, result)
        state.result = {
            "path": str(self.result_path),
            "sha256": _file_hash(self.result_path),
            "domain_count": len(domains),
            "subdomain_count": len(subdomains),
        }

    def _fail(
        self, state: CorporateReconState, step: str, exc: Exception
    ) -> CorporateReconState:
        state.current_step = step
        state.status = "failed"
        state.error = {
            "step": step,
            "type": type(exc).__name__,
            "message": str(exc)[:1000],
            "at": _utc_now(),
        }
        self._checkpoint(state)
        return state

    @staticmethod
    def _default_discovery_runner(
        state: CorporateReconState, out_dir: Path
    ) -> tuple[int, dict[str, Any]]:
        return run_pipeline(
            company=state.company,
            qid=state.qid,
            ticker=state.ticker,
            cik=state.cik,
            out_dir=out_dir,
        )

    @staticmethod
    def _default_domain_runner(
        state: CorporateReconState, workspace: Path
    ) -> tuple[int, dict[str, Any]]:
        here = Path(__file__).resolve()
        candidates = [
            Path("/app/skills/domain-enum/assets/run.py"),
            here.parents[2] / "skills" / "domain-enum" / "assets" / "run.py",
            here.parents[3] / "skills" / "domain-enum" / "assets" / "run.py",
        ]
        runner = next((path for path in candidates if path.is_file()), None)
        if runner is None:
            raise RuntimeError("domain-enum skill entry point was not found")
        command = (
            "pipeline --from-corp "
            f"--workspace {shlex.quote(str(workspace))} "
            f"--out-dir {shlex.quote(str(workspace))}"
        )
        completed = subprocess.run(
            [sys.executable, str(runner), command],
            check=False,
            capture_output=True,
            text=True,
        )
        summary: dict[str, Any] = {
            "status": "ok" if completed.returncode == 0 else "error",
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-2000:],
        }
        try:
            parsed = json.loads(completed.stdout)
            if isinstance(parsed, dict):
                summary.update(parsed)
        except json.JSONDecodeError:
            pass
        return completed.returncode, summary
