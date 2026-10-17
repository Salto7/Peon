"""Background Cognee improve (dream) cycles for Peon engagements."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.conf import settings

from orchestrator.crew.memory import MemoryService, job_session_id, project_dataset
from orchestrator.utils.service import SharedServiceBase

logger = logging.getLogger(__name__)

# Platform dataset only: lessons, gists and playbooks. Project datasets are
# engagement transcripts and are not a source of generic improvements.
_SUMMARY_DATASET = "peon"
_SUMMARY_QUERIES = (
    "suggested platform improvements tooling gaps missing tools fixes",
    "lessons learned failures pitfalls what should change",
    "playbooks recurring issues recommended changes",
)
_SUMMARY_BUDGET = 24_000
_SUMMARY_SYSTEM = (
    "You summarize suggested improvements that are already written in the "
    "memory excerpts. Use only those excerpts. Do not invent an improvement "
    "they do not support, and do not add target names, companies, domains, "
    "IP addresses, URLs or email addresses. Write plain text for an operator, "
    "with no markdown, asterisks or headings:\n"
    "1. Two to four sentences on the themes.\n"
    "2. A list. Each item starts with a dash and states one improvement, why "
    "the memory mentions it, and how often if the excerpts say.\n"
    "If the excerpts contain no improvements, say that in one sentence. "
    "Do not apply, edit, schedule or save anything."
)


def _recalled_chunks(result: dict[str, Any]) -> list[str]:
    rows = result.get("results")
    texts: list[str] = []
    if isinstance(rows, list):
        for item in rows:
            if isinstance(item, str) and item.strip():
                texts.append(item.strip())
                continue
            if not isinstance(item, dict):
                continue
            for key in ("memory", "text", "content", "answer", "snippet"):
                val = item.get(key)
                if isinstance(val, str) and val.strip():
                    texts.append(val.strip())
                    break
    if texts:
        return texts
    blob = str(result.get("text") or "").strip()
    return [blob] if blob else []


_MERGE_SYSTEM = (
    "Combine these partial summaries of the same memory into one summary. "
    "Keep every distinct improvement. Merge duplicates and keep the highest "
    "frequency when both mention one. Write plain text with no markdown, "
    "asterisks or headings: a short paragraph, then a dash list. Do not invent "
    "improvements, and do not apply, edit, schedule or save anything."
)


def _excerpt_batches(chunks: list[str]) -> list[str]:
    batches: list[list[str]] = []
    current: list[str] = []
    used = 0
    index = 0
    for chunk in chunks:
        index += 1
        block = f"[{index}]\n{chunk.strip()}"
        if current and used + len(block) > _SUMMARY_BUDGET:
            batches.append(current)
            current = []
            used = 0
        current.append(block)
        used += len(block)
    if current:
        batches.append(current)
    return ["\n\n".join(batch) for batch in batches]


def summarize_improvements() -> dict[str, Any]:
    """Read Cognee platform memory and return a display-only improvement summary.

    Does not write memory, cognify, or apply any suggestion.
    """
    from orchestrator.crew.cognee_backend import CogneeBackend
    from orchestrator.utils.llm import LLM_NOT_CONFIGURED, chat_text, llm_configured

    backend = CogneeBackend.shared()
    health = backend.health()
    if not health.get("ok"):
        return {
            "ok": False,
            "error": str(health.get("error") or "Cognee is unreachable")[:400],
        }

    chunks: list[str] = []
    seen: set[str] = set()
    errors: list[str] = []
    for query in _SUMMARY_QUERIES:
        result = backend.recall(query, datasets=[_SUMMARY_DATASET], top_k=50)
        if not result.get("ok"):
            err = str(result.get("error") or "").strip()
            if err and err not in errors:
                errors.append(err)
            continue
        for piece in _recalled_chunks(result):
            key = " ".join(piece.split())
            if not key or key in seen:
                continue
            seen.add(key)
            chunks.append(piece.strip())

    if not chunks:
        if errors:
            return {"ok": False, "error": errors[0][:400], "sources": 0}
        return {
            "ok": True,
            "text": "Cognee's platform memory has no lessons to summarize yet.",
            "sources": 0,
        }
    if not llm_configured():
        return {"ok": False, "error": LLM_NOT_CONFIGURED, "sources": len(chunks)}

    batches = _excerpt_batches(chunks)
    try:
        from orchestrator.utils import llm_usage

        partials: list[str] = []
        with llm_usage.purpose("dream_improvement_summary"):
            for batch in batches:
                part = chat_text(_SUMMARY_SYSTEM, batch).strip()
                if part:
                    partials.append(part)
            if len(partials) > 1:
                joined = "\n\n".join(
                    f"Part {index}\n{part}" for index, part in enumerate(partials, start=1)
                )
                text = chat_text(_MERGE_SYSTEM, joined).strip()
            else:
                text = partials[0] if partials else ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement summary failed: %s", exc)
        return {"ok": False, "error": str(exc)[:400], "sources": len(chunks)}
    if not text:
        return {
            "ok": False,
            "error": "The model returned an empty summary.",
            "sources": len(chunks),
        }
    return {"ok": True, "text": text, "sources": len(chunks)}


class DreamingService(SharedServiceBase):
    """Select due sessions and call Cognee ``improve`` offline."""

    def enabled(self) -> bool:
        return bool(getattr(settings, "PEON_DREAMING_ENABLED", True)) and MemoryService.enabled()

    def cursor_path(self) -> Path:
        root = Path(
            getattr(settings, "PLATFORM_WORKSPACE_DIR", "") or "workspaces/platform"
        ).resolve()
        path = root / "peon" / "dream_cursor.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def load_cursor(self) -> dict[str, Any]:
        path = self.cursor_path()
        if not path.is_file():
            return {"sessions": {}, "last_run_at": ""}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"sessions": {}, "last_run_at": ""}
        return data if isinstance(data, dict) else {"sessions": {}, "last_run_at": ""}

    def save_cursor(self, data: dict[str, Any]) -> None:
        path = self.cursor_path()
        try:
            path.write_text(
                json.dumps(data, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            logger.debug("dream cursor write failed: %s", exc)

    def run_cycle(self, *, limit: int = 20) -> dict[str, Any]:
        """Improve due project sessions; return structured report."""
        if not self.enabled():
            return {"ok": False, "skipped": True, "reason": "disabled"}
        now = datetime.now(UTC).isoformat(timespec="seconds")
        cursor = self.load_cursor()
        sessions = cursor.get("sessions") if isinstance(cursor.get("sessions"), dict) else {}
        due = self._due_sessions(limit=limit)
        improved: list[dict[str, Any]] = []
        errors: list[str] = []
        for item in due:
            dataset = item["dataset"]
            session_id = item["session_id"]
            result = MemoryService.improve_sessions(
                dataset=dataset,
                session_ids=[session_id],
                run_in_background=True,
            )
            row = {
                "dataset": dataset,
                "session_id": session_id,
                "project_id": item.get("project_id") or "",
                "ok": bool(result.get("ok")),
            }
            if result.get("ok"):
                sessions[session_id] = {
                    "dataset": dataset,
                    "project_id": item.get("project_id") or "",
                    "last_improved_at": now,
                    "job_updated_at": item.get("job_updated_at") or "",
                }
            else:
                err = str(result.get("error") or result.get("status_code") or "improve failed")
                row["error"] = err[:300]
                errors.append(f"{session_id}: {err[:200]}")
            improved.append(row)
        # Curate each finished agent's transcript into a gist (and supply the
        # transcript to Cognee). Gists are what planners later recall.
        gists = self.curate_gists(cursor, now=now)
        synthesis = self.synthesize(cursor, now=now)
        proposals: dict[str, Any] | None = None
        if (synthesis or {}).get("saved"):
            # Only worth asking when synthesis actually produced new playbooks
            # or a platform-improvements note to propose changes from.
            proposals = self.propose_catalog_changes()
        if any(g.get("gist") for g in gists) or (synthesis or {}).get("saved"):
            self.sync_platform_index()
        # Consolidate the platform dataset only when it actually gained
        # something; an unconditional pass re-distils whatever Q&A is cached.
        peon_result: dict[str, Any] = {"ok": True, "skipped": True, "reason": "no new gists"}
        if any(g.get("gist") for g in gists) or (synthesis or {}).get("saved"):
            peon_result = MemoryService.improve_sessions(
                dataset="peon",
                session_ids=None,
                run_in_background=True,
            )
        cursor["sessions"] = sessions
        cursor["last_run_at"] = now
        cursor["last_report"] = {
            "at": now,
            "improved": improved,
            "gists": gists,
            "synthesis": synthesis,
            "proposals": proposals,
            "peon_ok": bool(peon_result.get("ok")),
            "errors": errors[:20],
        }
        self.save_cursor(cursor)
        return {
            "ok": True,
            "at": now,
            "count": len(improved),
            "improved": improved,
            "gists": gists,
            "synthesis": synthesis,
            "proposals": proposals,
            "peon": peon_result,
            "errors": errors,
        }

    def clear(self, *, reindex: bool = True) -> dict[str, Any]:
        """Wipe Cognee, reset what the dream cycle tracked about it, optionally re-index.

        Peon's own records (memory notes, gists, playbooks) are untouched; they
        are the source of truth. With ``reindex`` the platform dataset is rebuilt
        from them straight away, otherwise the next dream cycle that writes a
        gist or playbook rebuilds it.
        """
        if not self.enabled():
            return {"ok": False, "skipped": True, "reason": "disabled"}
        from orchestrator.crew.cognee_backend import CogneeBackend

        wiped = CogneeBackend.shared().clear_all()
        cursor = self.load_cursor()
        cursor["sessions"] = {}  # the sessions they referred to are gone
        cursor.pop("last_report", None)
        self.save_cursor(cursor)
        index = self.sync_platform_index() if reindex and wiped.get("ok") else None
        return {"ok": bool(wiped.get("ok")), **wiped, "reindex": index}

    def sync_platform_index(self) -> dict[str, Any]:
        """Keep the Cognee retrieval index equal to the platform memory records."""
        try:
            from peon.projects.memory_store import sync_platform_dataset

            return sync_platform_dataset()
        except Exception as exc:  # noqa: BLE001
            logger.warning("platform index sync failed: %s", exc, exc_info=True)
            return {"ok": False}

    def improvement_summary(self) -> dict[str, Any]:
        """Display-only summary of improvements already stored in Cognee."""
        return summarize_improvements()

    def synthesize(self, cursor: dict[str, Any], *, now: str) -> dict[str, Any] | None:
        """Overall lessons learned across runs (playbooks + platform improvements)."""
        try:
            from peon.projects.gist import maybe_synthesize

            return maybe_synthesize(cursor, now=now)
        except Exception as exc:  # noqa: BLE001
            logger.warning("playbook synthesis failed: %s", exc, exc_info=True)
            return None

    def propose_catalog_changes(self) -> dict[str, Any] | None:
        """Ask the LLM to suggest role/skill catalog changes, staged for review."""
        try:
            from peon.projects.gist import propose_catalog_changes

            return propose_catalog_changes()
        except Exception as exc:  # noqa: BLE001
            logger.warning("catalog proposal generation failed: %s", exc, exc_info=True)
            return None

    def curate_gists(
        self, cursor: dict[str, Any], *, now: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Supply + curate finished agents not yet processed (bounded LLM calls)."""
        try:
            from peon.projects.db import ensure_project_database, iter_project_aliases
            from peon.projects.gist import process_job
            from peon.projects.models import Job, JobStatus, Project
        except Exception:  # noqa: BLE001
            return []
        done = cursor.setdefault("gists", {})
        terminal = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
        out: list[dict[str, Any]] = []

        def _llm_calls() -> int:
            # Trivial runs are skipped without an LLM call and don't count.
            return sum(1 for r in out if not r.get("skipped"))

        try:
            for project_id, alias in iter_project_aliases():
                if _llm_calls() >= limit:
                    break
                project = Project.objects.filter(pk=project_id).first()
                if project is None:
                    continue
                ensure_project_database(project_id)
                jobs = (
                    Job.objects.using(alias)
                    .filter(status__in=terminal)
                    .exclude(profile="governance")
                    .select_related("objective")
                    .order_by("-updated_at")[:60]
                )
                for job in jobs:
                    key = str(job.pk)
                    if key in done or _llm_calls() >= limit:
                        continue
                    row = process_job(project, alias, job)
                    if row.get("retry"):
                        out.append(row)  # LLM unavailable: try again next cycle
                        continue
                    done[key] = {
                        "at": now,
                        "scope": row.get("scope", ""),
                        "skipped": row.get("skipped", ""),
                    }
                    out.append(row)
        except Exception as exc:  # noqa: BLE001
            logger.warning("gist curation failed: %s", exc, exc_info=True)
        return out

    def status(self) -> dict[str, Any]:
        cursor = self.load_cursor()
        health = MemoryService.health()
        return {
            "enabled": self.enabled(),
            "cognee": health,
            "last_run_at": cursor.get("last_run_at") or "",
            "last_report": cursor.get("last_report") or {},
            "session_count": len(cursor.get("sessions") or {}),
        }

    def _due_sessions(self, *, limit: int = 20) -> list[dict[str, str]]:
        """Collect recent job/objective session ids from per-project SQLite files."""
        try:
            from peon.projects.db import ensure_project_database, iter_project_aliases
            from peon.projects.models import Job, JobStatus
        except Exception:
            return []

        terminal = {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }
        eligible = terminal | {JobStatus.RUNNING}
        cursor = self.load_cursor()
        seen = cursor.get("sessions") if isinstance(cursor.get("sessions"), dict) else {}
        # Gather recent jobs across project DBs (engagement rows never live on default).
        candidates: list[tuple[Any, str]] = []
        try:
            for project_id, alias in iter_project_aliases():
                ensure_project_database(project_id)
                rows = list(
                    Job.objects.using(alias)
                    .filter(status__in=eligible)
                    .order_by("-updated_at")
                    .only(
                        "id",
                        "status",
                        "objective_id",
                        "project_id",
                        "updated_at",
                    )[: max(1, min(80, limit * 3))]
                )
                for job in rows:
                    candidates.append((job, str(project_id)))
        except Exception as exc:
            logger.debug("dream due sessions query failed: %s", exc)
            return []

        def _updated_key(pair: tuple[Any, str]):
            ts = getattr(pair[0], "updated_at", None)
            if ts is None:
                return datetime(1970, 1, 1, tzinfo=UTC)
            if getattr(ts, "tzinfo", None) is None:
                return ts.replace(tzinfo=UTC)
            return ts

        candidates.sort(key=_updated_key, reverse=True)

        out: list[dict[str, str]] = []
        claimed: set[str] = set()
        for job, project_id in candidates:
            if not project_id:
                continue
            sid = job_session_id(job)
            if not sid or sid in claimed:
                continue
            job_updated = ""
            if getattr(job, "updated_at", None) is not None:
                try:
                    job_updated = job.updated_at.astimezone(UTC).isoformat()
                except Exception:
                    job_updated = str(job.updated_at)
            prev = seen.get(sid) if isinstance(seen.get(sid), dict) else None
            if prev:
                prev_updated = str(prev.get("job_updated_at") or "")
                # Skip until the Job row itself advances (new activity / finish).
                if prev_updated and job_updated and job_updated <= prev_updated:
                    continue
                if prev.get("last_improved_at") and not job_updated:
                    continue
                # Legacy cursor (no job_updated_at): terminal sessions stay done.
                if not prev_updated and job.status in terminal and prev.get("last_improved_at"):
                    continue
            claimed.add(sid)
            out.append(
                {
                    "dataset": project_dataset(project_id),
                    "session_id": sid,
                    "project_id": project_id,
                    "job_updated_at": job_updated,
                }
            )
            if len(out) >= limit:
                break
        return out
