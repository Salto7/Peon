"""Peon semantic memory — Cognee-backed (replaces CrewAI LanceDB memory)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from django.conf import settings

from orchestrator.crew.cognee_backend import CogneeBackend
from orchestrator.utils.service import SharedServiceBase

logger = logging.getLogger(__name__)


def memory_enabled() -> bool:
    return bool(getattr(settings, "COGNEE_ENABLED", True))


def peon_memory_enabled() -> bool:
    """Platform dataset ``peon`` writes/recalls (cross-project lessons)."""
    return memory_enabled() and bool(getattr(settings, "PEON_MEMORY_ENABLED", True))


def memory_required_for_project(project: Any | None) -> bool:
    """Durable projects always keep memory on; timed follows COGNEE_ENABLED."""
    if not memory_enabled():
        return False
    if project is None:
        return True
    kind = getattr(project, "kind", "") or ""
    if str(kind).strip().lower() == "durable":
        return True
    return True


def project_dataset(project_id: str) -> str:
    pid = str(project_id or "").strip()
    return f"project_{pid}" if pid else "peon"


def job_session_id(job: Any) -> str:
    oid = str(getattr(job, "objective_id", "") or "").strip()
    jid = str(getattr(job, "id", "") or "").strip()
    if oid:
        return f"objective_{oid}"
    if jid:
        return f"job_{jid}"
    return ""


def record_payload(record: Any) -> dict[str, Any]:
    """Labeled JSON document for Cognee (raw structured feed)."""
    payload = getattr(record, "payload", None)
    if not isinstance(payload, dict):
        payload = {}
    return {
        "kind": str(getattr(record, "category", "") or "action").strip() or "action",
        "summary": str(getattr(record, "summary", "") or "").strip(),
        "event": str(getattr(record, "event", "") or "").strip(),
        "role_id": str(getattr(record, "role_id", "") or "").strip(),
        "project_id": str(getattr(record, "project_id", "") or "").strip(),
        "objective_id": str(getattr(record, "objective_id", "") or "").strip(),
        "job_id": str(getattr(record, "job_id", "") or "").strip(),
        "created_at": str(getattr(record, "created_at", "") or "").strip(),
        "payload": payload,
        "indications": _indications(record),
    }


def _indications(record: Any) -> list[str]:
    cat = str(getattr(record, "category", "") or "").strip().lower()
    event = str(getattr(record, "event", "") or "").strip().lower()
    tags: list[str] = []
    if cat:
        tags.append(cat)
    if cat in {"error", "gap"} or "fail" in event:
        tags.append("failure")
    if cat in {"action", "method"} or "prompt" in event or event in {
        "run_cli",
        "steer",
        "operator",
    }:
        tags.append("prompt")
    if cat == "outcome":
        tags.append("outcome")
    if cat == "replan":
        tags.append("replan")
    if cat == "handoff":
        tags.append("handoff")
    return tags


def remember_transcript(job: Any, record: Any) -> None:
    """Feed a raw labeled transcript record into Cognee (session + audit)."""
    if not memory_enabled():
        return
    project_id = str(getattr(job, "project_id", "") or getattr(record, "project_id", "") or "")
    if not project_id:
        return
    doc = record_payload(record)
    backend = CogneeBackend.shared()
    backend.remember(
        doc,
        dataset_name=project_dataset(project_id),
        session_id=job_session_id(job),
        self_improvement=False,
        run_in_background=True,
    )


def remember_peon(
    content: str,
    *,
    category: str = "digest",
    event: str = "",
    project_id: str = "",
    refs: list[str] | None = None,
) -> bool:
    """Write a labeled document into the platform ``peon`` dataset."""
    if not peon_memory_enabled():
        return False
    text = (content or "").strip()
    if not text:
        return False
    doc = {
        "kind": str(category or "digest").strip() or "digest",
        "summary": text[:8000],
        "event": str(event or "").strip(),
        "project_id": str(project_id or "").strip(),
        "refs": list(refs or []),
        "indications": [str(category or "digest"), "peon"],
    }
    result = CogneeBackend.shared().remember(
        doc,
        dataset_name="peon",
        session_id="",
        self_improvement=False,
        run_in_background=True,
    )
    return bool(result.get("ok"))


def recall_for_new_project(
    *,
    title: str = "",
    summary: str = "",
    scope_hints: list[str] | None = None,
    top_k: int = 8,
) -> str:
    """Recall peon-wide lessons to bootstrap a newly created project."""
    if not peon_memory_enabled():
        return ""
    bits = [str(title or "").strip(), str(summary or "").strip()]
    for hint in scope_hints or []:
        h = str(hint or "").strip()
        if h:
            bits.append(h)
    query = " ".join(bits).strip() or "engagement lessons methods failures replans"
    result = CogneeBackend.shared().recall(
        query,
        datasets=["peon"],
        top_k=top_k,
    )
    if not result.get("ok") or result.get("build_failed"):
        logger.warning(
            "peon memory bootstrap recall skipped: %s",
            (result.get("error") or "unavailable")[:300],
        )
        return ""
    return str(result.get("text") or "").strip()


def bootstrap_path(project_id: str) -> Path | None:
    if not project_id:
        return None
    try:
        from orchestrator.utils.paths import safe_workspace_key

        root = Path(
            getattr(settings, "PROJECT_WORKSPACES_DIR", "") or "workspaces"
        ).resolve()
        return root / safe_workspace_key(str(project_id)) / "memory_bootstrap.md"
    except Exception:
        return None


def write_project_bootstrap(project_id: str, text: str) -> Path | None:
    path = bootstrap_path(project_id)
    body = (text or "").strip()
    if path is None or not body:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# Memory bootstrap (Cognee peon recall)\n\n" + body + "\n",
            encoding="utf-8",
        )
        return path
    except OSError as exc:
        logger.debug("memory bootstrap write failed: %s", exc)
        return None


def build_lab_catalog() -> dict[str, Any]:
    """Enrich Cognee datasets/sessions with Peon project + job labels for Lab UI."""
    backend = CogneeBackend.shared()
    health = backend.health()
    cognee_ds = backend.datasets()
    cognee_sess = backend.sessions(range_="all", limit=200)

    projects: dict[str, Any] = {}
    try:
        from peon.projects.models import Project

        for p in Project.objects.using("default").all().only("id", "title", "status"):
            projects[str(p.id)] = p
    except Exception as exc:
        logger.debug("lab_catalog projects: %s", exc)

    datasets_by_name: dict[str, dict[str, Any]] = {}

    def _add_dataset(name: str, *, project_id: str = "", source: str = "") -> None:
        key = str(name or "").strip()
        if not key:
            return
        row = datasets_by_name.get(key) or {
            "name": key,
            "project_id": "",
            "project_title": "",
            "label": key,
            "source": source or "cognee",
        }
        if project_id and not row.get("project_id"):
            row["project_id"] = project_id
            proj = projects.get(project_id)
            title = str(getattr(proj, "title", "") or "").strip()
            row["project_title"] = title
            row["label"] = f"{title} · {key}" if title else key
        elif key == "peon":
            row["label"] = "peon — platform memory"
            row["source"] = row.get("source") or "platform"
        datasets_by_name[key] = row

    _add_dataset("peon", source="platform")
    for pid, proj in projects.items():
        _add_dataset(project_dataset(pid), project_id=pid, source="project")

    for raw in cognee_ds.get("datasets") or []:
        if isinstance(raw, str):
            _add_dataset(raw, source="cognee")
            continue
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("id") or "").strip()
        pid = ""
        if name.startswith("project_"):
            pid = name[len("project_") :]
        _add_dataset(name, project_id=pid if pid in projects else "", source="cognee")

    sessions_by_id: dict[str, dict[str, Any]] = {}

    def _add_session(
        session_id: str,
        *,
        dataset: str = "",
        project_id: str = "",
        job_title: str = "",
        status: str = "",
        source: str = "",
    ) -> None:
        sid = str(session_id or "").strip()
        if not sid:
            return
        ds = str(dataset or "").strip()
        pid = str(project_id or "").strip()
        if not pid and ds.startswith("project_"):
            maybe = ds[len("project_") :]
            if maybe in projects:
                pid = maybe
        if not ds and pid:
            ds = project_dataset(pid)
        proj = projects.get(pid)
        ptitle = str(getattr(proj, "title", "") or "").strip()
        jtitle = str(job_title or "").strip()
        label_bits = [b for b in (ptitle, jtitle or sid, status) if b]
        row = sessions_by_id.get(sid) or {
            "session_id": sid,
            "dataset": ds or "peon",
            "project_id": pid,
            "project_title": ptitle,
            "job_title": jtitle,
            "status": str(status or "").strip(),
            "label": " · ".join(label_bits) if label_bits else sid,
            "source": source or "peon",
        }
        if ds and (not row.get("dataset") or row["dataset"] == "peon"):
            row["dataset"] = ds
        if pid and not row.get("project_id"):
            row["project_id"] = pid
            row["project_title"] = ptitle
        if jtitle and not row.get("job_title"):
            row["job_title"] = jtitle
        if status and not row.get("status"):
            row["status"] = str(status).strip()
        row["label"] = " · ".join(
            b
            for b in (
                row.get("project_title") or ptitle,
                row.get("job_title") or jtitle or sid,
                row.get("status") or status,
            )
            if b
        )
        sessions_by_id[sid] = row

    for raw in cognee_sess.get("sessions") or []:
        if not isinstance(raw, dict):
            continue
        sid = str(
            raw.get("session_id") or raw.get("id") or raw.get("name") or ""
        ).strip()
        ds = str(
            raw.get("dataset") or raw.get("dataset_name") or raw.get("datasetName") or ""
        ).strip()
        _add_session(
            sid,
            dataset=ds,
            status=str(raw.get("status") or ""),
            source="cognee",
        )

    try:
        from peon.projects.db import using_project
        from peon.projects.models import Job

        for pid, _proj in projects.items():
            try:
                with using_project(pid):
                    jobs = list(
                        Job.objects.order_by("-updated_at").only(
                            "id",
                            "title",
                            "status",
                            "objective_id",
                            "project_id",
                            "updated_at",
                        )[:80]
                    )
            except Exception as exc:
                logger.debug("lab_catalog jobs project=%s: %s", pid, exc)
                continue
            for job in jobs:
                _add_session(
                    job_session_id(job),
                    dataset=project_dataset(pid),
                    project_id=pid,
                    job_title=str(getattr(job, "title", "") or "").strip(),
                    status=str(getattr(job, "status", "") or "").strip(),
                    source="job",
                )
    except Exception as exc:
        logger.debug("lab_catalog jobs: %s", exc)

    datasets = sorted(
        datasets_by_name.values(),
        key=lambda r: (0 if r["name"] == "peon" else 1, r.get("label") or r["name"]),
    )
    sessions = sorted(
        sessions_by_id.values(),
        key=lambda r: (
            r.get("project_title") or "",
            r.get("job_title") or r.get("session_id") or "",
        ),
    )
    return {
        "ok": bool(health.get("ok")),
        "cognee": health,
        "datasets": datasets,
        "sessions": sessions,
        "datasets_error": str(cognee_ds.get("error") or ""),
        "sessions_error": str(cognee_sess.get("error") or ""),
    }


class MemoryService(SharedServiceBase):
    """Cognee-backed project and Peon memory lifecycle."""

    enabled = staticmethod(memory_enabled)
    peon_enabled = staticmethod(peon_memory_enabled)
    required_for_project = staticmethod(memory_required_for_project)
    remember_transcript = staticmethod(remember_transcript)
    remember_peon = staticmethod(remember_peon)
    recall_for_new_project = staticmethod(recall_for_new_project)
    write_project_bootstrap = staticmethod(write_project_bootstrap)

    @classmethod
    def health(cls) -> dict[str, Any]:
        return CogneeBackend.shared().health()

    @classmethod
    def recall(
        cls,
        query: str,
        *,
        datasets: list[str] | None = None,
        session_id: str = "",
        top_k: int = 8,
    ) -> str:
        return str(
            CogneeBackend.shared()
            .recall(query, datasets=datasets, session_id=session_id, top_k=top_k)
            .get("text")
            or ""
        )

    @classmethod
    def improve_sessions(
        cls,
        *,
        dataset: str = "",
        datasets: list[str] | None = None,
        all_datasets: bool = False,
        session_ids: list[str] | None = None,
        run_in_background: bool = True,
    ) -> dict[str, Any]:
        backend = CogneeBackend.shared()
        if all_datasets:
            rows = backend.datasets().get("datasets") or []
            names = [
                str(r.get("name") or "").strip()
                for r in rows
                if isinstance(r, dict) and str(r.get("name") or "").strip()
            ]
            if not names:
                names = ["peon"]
            return backend.improve_many(
                datasets=names,
                session_ids=session_ids,
                run_in_background=run_in_background,
            )
        many = [str(d).strip() for d in (datasets or []) if str(d).strip()]
        if many:
            return backend.improve_many(
                datasets=many,
                session_ids=session_ids,
                run_in_background=run_in_background,
            )
        return backend.improve(
            dataset=str(dataset or "peon").strip() or "peon",
            session_ids=session_ids,
            run_in_background=run_in_background,
        )

    @classmethod
    def datasets(cls) -> dict[str, Any]:
        return CogneeBackend.shared().datasets()

    @classmethod
    def sessions(cls, **kwargs: Any) -> dict[str, Any]:
        return CogneeBackend.shared().sessions(**kwargs)

    @classmethod
    def lab_catalog(cls) -> dict[str, Any]:
        """Datasets + sessions for Lab Dream pickers (Cognee + Peon project names)."""
        return build_lab_catalog()

    # --- Compatibility stubs (CrewAI memory removed) ---

    @staticmethod
    def build_project(project_id: str, *, project: Any | None = None) -> None:
        del project_id, project
        return None

    @staticmethod
    def for_job(job: Any) -> None:
        del job
        return None

    @staticmethod
    def from_scope(scope: Any) -> None:
        del scope
        return None

    @staticmethod
    def remember_into(target: Any, content: str, meta: dict[str, Any] | None = None) -> bool:
        """Legacy hook: map into peon/project Cognee remember."""
        del target
        meta = meta or {}
        project_id = str(meta.get("project_id") or "")
        text = (content or "").strip()
        if not text:
            return False
        if project_id:
            doc = {
                "kind": str(meta.get("category") or "method"),
                "summary": text[:8000],
                "event": str(meta.get("event") or ""),
                "role_id": str(meta.get("role_id") or ""),
                "project_id": project_id,
                "indications": [str(meta.get("category") or "method")],
                "meta": {k: v for k, v in meta.items() if k not in {"category"}},
            }
            return bool(
                CogneeBackend.shared()
                .remember(
                    doc,
                    dataset_name=project_dataset(project_id),
                    self_improvement=False,
                )
                .get("ok")
            )
        return remember_peon(
            text,
            category=str(meta.get("category") or "digest"),
            event=str(meta.get("event") or ""),
            project_id=str(meta.get("project_id") or ""),
        )
