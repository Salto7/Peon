"""Background Cognee improve (dream) cycles for Peon engagements."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.conf import settings

from orchestrator.crew.memory import MemoryService, job_session_id, project_dataset
from orchestrator.utils.service import SharedServiceBase

logger = logging.getLogger(__name__)


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
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
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
        # Also refresh peon dataset consolidation periodically.
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
            "peon_ok": bool(peon_result.get("ok")),
            "errors": errors[:20],
        }
        self.save_cursor(cursor)
        return {
            "ok": True,
            "at": now,
            "count": len(improved),
            "improved": improved,
            "peon": peon_result,
            "errors": errors,
        }

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
                return datetime(1970, 1, 1, tzinfo=timezone.utc)
            if getattr(ts, "tzinfo", None) is None:
                return ts.replace(tzinfo=timezone.utc)
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
                    job_updated = job.updated_at.astimezone(timezone.utc).isoformat()
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
                if (
                    not prev_updated
                    and job.status in terminal
                    and prev.get("last_improved_at")
                ):
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
