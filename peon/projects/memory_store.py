"""Agent memory: typed, named, always-indexed notes (Claude-style) over Cognee.

* ``MemoryEntry`` rows are the source of truth, so entries can be updated,
  forgotten and listed exactly. Cognee receives a mirrored document for graph
  recall and the dreaming/improve cycle.
* Every agent starts with a compact **index** of what is known (one line per
  entry); details come back through ``recall_memory``. The engagement manager
  also gets the full text of operator feedback and decisions.
* Two scopes: ``project`` (this engagement — may be target-specific) and
  ``platform`` (cross-engagement lessons — must be generic; guarded against
  leaking target data).
* Retrieval is BM25 (``rank-bm25``) over the local entries, merged with a
  Cognee graph completion when the service is reachable.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from django.utils import timezone as dj_tz
from rank_bm25 import BM25Okapi

from peon.projects.models import Job, MemoryEntry, Project

logger = logging.getLogger(__name__)

KINDS = tuple(k.value for k in MemoryEntry.Kind)
PLATFORM_KINDS = frozenset({"lesson", "reference"})
MAX_BODY = 4_000
MAX_DESCRIPTION = 240
INDEX_PROJECT_LIMIT = 30
INDEX_PLATFORM_LIMIT = 8
MANAGER_FULL_CHARS = 3_500

_WORD = re.compile(r"[a-z0-9_]{2,}")
_SLUG = re.compile(r"[^a-z0-9]+")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_URL = re.compile(r"https?://\S+", re.I)


# ── addressing / helpers ─────────────────────────────────────────────────


def _alias(project: Project | None) -> str:
    if project is None:
        return "default"
    from peon.projects.db import ensure_project_database

    return ensure_project_database(project.pk)


def _scope_qs(project: Project | None):
    """Active entries of one scope (project DB, or platform DB when None)."""
    alias = _alias(project)
    qs = MemoryEntry.objects.using(alias).filter(status=MemoryEntry.Status.ACTIVE)
    if project is None:
        return qs.filter(project__isnull=True)
    from peon.projects.db import using_project

    with using_project(project.pk):
        return qs.filter(project_id=project.pk)


def slugify(name: str) -> str:
    return _SLUG.sub("-", (name or "").strip().lower()).strip("-")[:64]


def _tokens(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def target_specific_reason(text: str, project: Project | None = None) -> str:
    """Why ``text`` must not become a platform-wide lesson ('' when it is fine)."""
    blob = text or ""
    if _IPV4.search(blob):
        return "contains an IP address"
    if _EMAIL.search(blob):
        return "contains an email address"
    if _URL.search(blob):
        return "contains a URL"
    roe = getattr(project, "roe", None) if project is not None else None
    if roe is not None:
        from peon.projects.target_shapes import coerce_targets

        low = blob.lower()
        for attr in ("seed", "in_scope"):
            for t in coerce_targets(getattr(roe, attr, None)):
                v = str(t.get("value") or "").lower()
                if len(v) >= 4 and v in low:
                    return f"mentions in-scope target {v!r}"
    return ""


# ── write path ───────────────────────────────────────────────────────────


def save(
    project: Project | None,
    *,
    name: str,
    kind: str,
    description: str,
    body: str = "",
    role_id: str = "",
    job: Job | None = None,
    created_by: str = "",
) -> tuple[MemoryEntry, bool]:
    """Create or update a memory. Returns ``(entry, created)``; raises ``ValueError``."""
    slug = slugify(name)
    if not slug:
        raise ValueError("name is required")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of: {', '.join(KINDS)}")
    desc = " ".join((description or "").split())[:MAX_DESCRIPTION]
    if not desc:
        raise ValueError("description (one-line hook) is required")
    text = (body or "").strip()[:MAX_BODY]
    if project is None:
        if kind not in PLATFORM_KINDS:
            raise ValueError(
                "platform memory is for generic lessons/references only; "
                f"save {kind!r} entries in the project scope"
            )
        reason = target_specific_reason(f"{desc}\n{text}")
        if reason:
            raise ValueError(
                f"platform lessons must be generic ({reason}); "
                "generalise it or save it in the project scope"
            )
    else:
        reason = ""

    alias = _alias(project)
    qs = MemoryEntry.objects.using(alias)
    existing = (
        qs.filter(project_id=project.pk, name=slug).first()
        if project is not None
        else qs.filter(project__isnull=True, name=slug).first()
    )
    if existing is not None:
        existing.kind, existing.description, existing.body = kind, desc, text
        existing.status = MemoryEntry.Status.ACTIVE
        if role_id:
            existing.role_id = role_id[:64]
        existing.save(using=alias)
        entry, created = existing, False
    else:
        entry = MemoryEntry(
            project=project,
            name=slug,
            kind=kind,
            description=desc,
            body=text,
            role_id=(role_id or "")[:64],
            created_by=(created_by or "")[:96],
            # Platform rows live in the platform DB and cannot reference a job
            # that lives in a project DB.
            source_job=job if project is not None else None,
        )
        entry.save(using=alias)
        created = True
    _mirror(entry, project)
    return entry, created


def forget(project: Project | None, name: str) -> bool:
    """Archive a memory (it leaves the index and recall)."""
    slug = slugify(name)
    alias = _alias(project)
    qs = MemoryEntry.objects.using(alias).filter(name=slug)
    qs = qs.filter(project_id=project.pk) if project is not None else qs.filter(project__isnull=True)
    n = qs.update(status=MemoryEntry.Status.ARCHIVED, updated_at=dj_tz.now())
    if n:
        _mirror_tombstone(project, slug)
    return bool(n)


def record_feedback(project: Project, text: str, *, source: str = "chat") -> MemoryEntry | None:
    """Capture operator guidance as durable feedback (deduplicated)."""
    body = " ".join((text or "").split())
    if len(body) < 12 or body.endswith("?"):
        return None
    digest = hashlib.sha1(body.lower().encode()).hexdigest()[:8]
    first = re.split(r"(?<=[.!?])\s", body, maxsplit=1)[0]
    try:
        entry, _ = save(
            project,
            name=f"feedback-{digest}",
            kind="feedback",
            description=first[:200],
            body=(
                f"{text.strip()}\n\n"
                f"**Why:** instruction from the operator ({source}).\n"
                "**How to apply:** honor this in all later work on this engagement "
                "unless the operator revises it; it overrides your own preference."
            ),
            created_by="operator",
        )
        return entry
    except ValueError:
        return None


def record_project_digest(project: Project, status: str) -> MemoryEntry | None:
    """Auto-capture a project-scope summary when an engagement reaches a
    terminal status. Agents can voluntarily call ``save_memory`` for anything
    they consider durable, but most engagements never do — without this,
    the memory panel and the dream/improve cycle stay empty even for
    finished work. One row per project (re-run updates it in place)."""

    from peon.projects.models import Finding, Objective

    alias = _alias(project)
    objectives = list(
        Objective.objects.using(alias)
        .filter(project_id=project.pk)
        .order_by("seq")
        .values_list("title", "status")
    )
    findings = list(
        Finding.objects.using(alias)
        .filter(project_id=project.pk)
        .values_list("title", "severity", "asset_type")
    )
    if not objectives and not findings:
        return None

    sev_counts: dict[str, int] = {}
    for _, sev, _ in findings:
        sev_counts[sev] = sev_counts.get(sev, 0) + 1
    sev_line = ", ".join(f"{n} {s}" for s, n in sorted(sev_counts.items())) or "none"

    obj_lines = "\n".join(f"- [{s}] {t}" for t, s in objectives) or "_none_"
    finding_lines = (
        "\n".join(f"- [{sev}] {t}" + (f" ({kind})" if kind else "") for t, sev, kind in findings[:50])
        or "_none_"
    )

    desc = f"Engagement finished ({status}): {len(objectives)} objective(s), findings: {sev_line}."
    body = (
        f"**Status:** {status}\n\n"
        f"**Objectives:**\n{obj_lines}\n\n"
        f"**Findings ({sev_line}):**\n{finding_lines}\n\n"
        "**Why:** auto-captured project digest so this engagement's outcome is "
        "recorded even when no agent called save_memory.\n"
        "**How to apply:** read before replanning or starting a similar "
        "engagement against the same scope."
    )
    try:
        entry, _ = save(
            project,
            name="project-digest",
            kind="fact",
            description=desc[:MAX_DESCRIPTION],
            body=body,
            created_by="system",
        )
        return entry
    except ValueError:
        logger.warning("record_project_digest failed for project=%s", project.pk)
        return None


# ── Cognee mirror ────────────────────────────────────────────────────────


def _dataset(project: Project | None) -> str:
    from orchestrator.crew.memory import project_dataset

    return project_dataset(str(project.pk)) if project is not None else "peon"


def _mirror(entry: MemoryEntry, project: Project | None) -> None:
    try:
        from orchestrator.crew.cognee_backend import CogneeBackend
        from orchestrator.crew.memory import memory_enabled

        if not memory_enabled():
            return
        CogneeBackend.shared().remember(
            {
                "kind": "memory",
                "memory_kind": entry.kind,
                "name": entry.name,
                "summary": entry.description,
                "body": entry.body,
                "role_id": entry.role_id,
                "project_id": str(project.pk) if project is not None else "",
                "status": "active",
                "indications": ["memory", entry.kind],
            },
            dataset_name=_dataset(project),
            session_id="memory",
            self_improvement=False,
            run_in_background=True,
        )
    except Exception:  # noqa: BLE001 — Cognee is an accelerator, never a gate
        logger.debug("memory mirror failed", exc_info=True)


def _mirror_tombstone(project: Project | None, slug: str) -> None:
    try:
        from orchestrator.crew.cognee_backend import CogneeBackend
        from orchestrator.crew.memory import memory_enabled

        if memory_enabled():
            CogneeBackend.shared().remember(
                {"kind": "memory", "name": slug, "status": "archived",
                 "summary": f"Memory {slug!r} was retired; disregard earlier notes under this name."},
                dataset_name=_dataset(project),
                session_id="memory",
                run_in_background=True,
            )
    except Exception:  # noqa: BLE001
        logger.debug("memory tombstone failed", exc_info=True)


# ── read path ────────────────────────────────────────────────────────────


def _entries(project: Project | None) -> list[MemoryEntry]:
    out = list(_scope_qs(project))
    if project is not None:
        out += list(_scope_qs(None))
    return out


def _mark_recalled(entries: list[MemoryEntry]) -> None:
    now = dj_tz.now()
    for e in entries:
        try:
            alias = "default" if e.project_id is None else _alias_by_id(e.project_id)
            MemoryEntry.objects.using(alias).filter(pk=e.pk).update(
                recall_count=e.recall_count + 1, last_recalled_at=now
            )
        except Exception:  # noqa: BLE001
            logger.debug("mark recalled failed", exc_info=True)


def _alias_by_id(project_id: Any) -> str:
    from peon.projects.db import ensure_project_database

    return ensure_project_database(project_id)


def search(
    project: Project | None,
    query: str,
    *,
    role_id: str = "",
    limit: int = 5,
    mark: bool = True,
) -> list[tuple[MemoryEntry, float]]:
    """Rank this engagement's and the platform's memories for ``query`` (BM25)."""
    entries = _entries(project)
    if not entries:
        return []
    docs = [
        _tokens(f"{e.name.replace('-', ' ')} {e.description} {e.body} {e.kind} {e.role_id}")
        for e in entries
    ]
    q = _tokens(query)
    scores = [0.0] * len(entries)
    if q and any(docs):
        scores = list(BM25Okapi([d or ["_"] for d in docs]).get_scores(q))
    ranked: list[tuple[MemoryEntry, float]] = []
    for e, s in zip(entries, scores):
        boost = 1.0
        if role_id and e.role_id == role_id:
            boost += 0.25
        if e.kind == "feedback":
            boost += 0.2  # operator guidance outranks incidental notes
        ranked.append((e, float(s) * boost))
    ranked.sort(key=lambda r: (r[1], r[0].updated_at), reverse=True)
    top = [r for r in ranked if r[1] > 0][:limit] or ranked[: min(limit, 3)]
    if mark:
        _mark_recalled([e for e, _ in top])
    return top


def _fmt_entry(e: MemoryEntry, *, body_chars: int = 600) -> str:
    scope = "engagement" if e.project_id else "platform"
    head = f"- [{e.kind}] {e.name} ({scope}, updated {e.updated_at:%Y-%m-%d}) — {e.description}"
    body = (e.body or "").strip()
    return head + (f"\n  {body[:body_chars]}" if body else "")


def recall_text(project: Project | None, query: str, *, role_id: str = "") -> str:
    """Answer for the ``recall_memory`` tool."""
    hits = search(project, query, role_id=role_id, limit=5)
    parts: list[str] = []
    if hits:
        parts.append("Stored memories (most relevant first):\n" + "\n".join(_fmt_entry(e) for e, _ in hits))
    graph = ""
    try:
        from orchestrator.crew.memory import MemoryService, memory_enabled

        if memory_enabled() and (query or "").strip():
            datasets = ([_dataset(project)] if project is not None else []) + ["peon"]
            graph = (MemoryService.recall(query, datasets=datasets, top_k=4) or "").strip()
    except Exception:  # noqa: BLE001
        graph = ""
    if graph:
        parts.append("Related knowledge (Cognee graph recall):\n" + graph[:1800])
    if not parts:
        return "No relevant memory found."
    parts.append(
        "Memories are point-in-time notes — check them against the current "
        "state before relying on them, and `save_memory` to update one."
    )
    return "\n\n".join(parts)


def index_block(project: Project, *, role_id: str = "", manager: bool = False) -> str:
    """The memory prelude every agent starts with."""
    mine = sorted(
        _scope_qs(project),
        key=lambda e: (e.kind != "feedback", -e.recall_count, -e.updated_at.timestamp()),
    )
    plat = [
        e
        for e in _scope_qs(None)
        if not role_id or not e.role_id or e.role_id == role_id
    ]
    plat.sort(key=lambda e: (e.role_id != role_id, -e.recall_count, -e.updated_at.timestamp()))
    lines = [
        "## Memory",
        "Notes carried over from earlier work (point-in-time — verify before "
        "relying on them). `recall_memory` fetches detail, `save_memory` records "
        "something durable you learned, `forget_memory` retires a stale note.",
    ]
    if mine:
        lines.append("### This engagement")
        lines += [f"- [{e.kind}] {e.name} — {e.description}" for e in mine[:INDEX_PROJECT_LIMIT]]
        if len(mine) > INDEX_PROJECT_LIMIT:
            lines.append(f"- … {len(mine) - INDEX_PROJECT_LIMIT} more (use recall_memory)")
    if plat:
        lines.append("### Platform lessons")
        lines += [f"- [{e.kind}] {e.name} — {e.description}" for e in plat[:INDEX_PLATFORM_LIMIT]]
    if manager:
        full = [e for e in mine if e.kind in {"feedback", "decision"}]
        if full:
            lines.append("### Standing instructions & decisions (full text)")
            budget = MANAGER_FULL_CHARS
            for e in full:
                chunk = f"**{e.name}** ({e.kind}): {(e.body or e.description).strip()}"
                if budget <= 0:
                    break
                lines.append(chunk[:budget])
                budget -= len(chunk)
    if manager:
        recap = _bootstrap_recall(project)
        if recap:
            lines.append("### Recalled from earlier engagements (Cognee, unverified)")
            lines.append(recap)
    if not mine and not plat and not (manager and "### Recalled" in "\n".join(lines)):
        lines.append("(no memories yet — save durable discoveries as you work)")
    return "\n".join(lines)


def _bootstrap_recall(project: Project, limit: int = 1_800) -> str:
    """Graph recall written when the project was created (``memory_bootstrap.md``)."""
    try:
        from orchestrator.crew.memory import bootstrap_path

        path = bootstrap_path(str(project.pk))
        if path is None or not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""
    text = text.split("\n", 2)[-1].strip() if text.startswith("# Memory bootstrap") else text.strip()
    return text[:limit]
