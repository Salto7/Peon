"""Platform catalog: the only application access to catalog tables.

Roles, skills, MCP server specs, file bodies, and role mappings live in
``workspaces/platform/db.sqlite3``. Callers use this module. They do not query
those models directly.

``skills/`` is a one-time bootstrap when the skill table is empty. Pack type
(peon vs claude) is inferred per directory. After that the database is the
registry.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.db import IntegrityError, transaction

from orchestrator.utils.service import SharedServiceBase

logger = logging.getLogger(__name__)

OWNER_SKILL = "skill"
OWNER_MCP = "mcp"
OWNER_ROLE = "role"

# Platform-core skills: load-bearing for every project (project-manager and
# analyzer are the bookend roles' own skills) or hard-imported by Toolsmith's
# authoring pipeline (skill-writer, tools-suggestor — see
# orchestrator/toolsmith/system_skills.py). Deleting one breaks the platform,
# so — like a protected role — the catalog refuses to.
CORE_SKILL_IDS = frozenset({"analyzer", "project-manager", "skill-writer", "tools-suggestor"})


def _roleless_core_skill_ids() -> frozenset[str]:
    """Of ``CORE_SKILL_IDS``, the two Toolsmith calls by id directly and that
    are therefore never dispatched to an engagement role at all (the other
    two are the bookends' own lead skills and must keep binding to their
    role). Reads the live ids from ``system_skills`` rather than repeating
    their default string here — a renamed authoring skill stays excluded.
    """
    from orchestrator.toolsmith.system_skills import skill_writer_skill, tools_suggestor_skill

    return frozenset({skill_writer_skill(), tools_suggestor_skill()})


_SKILLS_FINGERPRINT = ""  # last on-disk skill state reconciled by this process
_revision: float = 0.0


@dataclass(frozen=True)
class Binding:
    role_id: str
    owner_kind: str
    owner_id: str
    score: float = 1.0
    lead: bool = False
    # Per-binding tool curation for MCP owners: {"mode": "include"|"exclude",
    # "values": [tool names]}. None (the default) means every tool the
    # server advertises is callable — unset, not an empty allow-list.
    tools: dict[str, Any] | None = None
    # Phase gate for MCP or skill owners: a KillChainPhase value, or "" for no
    # gate. The owning Objective must have reached this phase before the
    # owner's tools/binaries are usable (Core 1.3 — see
    min_phase: str = ""


@dataclass(frozen=True)
class SkillRecord:
    skill_id: str
    provider: str
    description: str
    body: str
    manifest: dict[str, Any]
    setup: dict[str, Any] | None
    files: dict[str, str] = field(default_factory=dict)
    bindings: tuple[Binding, ...] = ()


@dataclass(frozen=True)
class McpRecord:
    mcp_id: str
    description: str
    transport: str
    command: str
    args: tuple[str, ...]
    cwd: str
    install: dict[str, Any] | None
    env_schema: tuple[tuple[str, bool], ...]
    enabled: bool
    files: dict[str, str] = field(default_factory=dict)
    bindings: tuple[Binding, ...] = ()
    reference_hosts: tuple[str, ...] = ()
    # Full per-field metadata (type/description/default/choices) behind
    # env_schema's plain (name, required) pairs — see orchestrator/mcp/parse.py.
    env_schema_full: tuple[dict[str, Any], ...] = ()
    # Set only for http/streamable-http/sse transport — see orchestrator/mcp/parse.py.
    url: str = ""


_README_NAMES = frozenset({"readme.md", "readme.rst", "readme.txt", "readme"})
_MCP_ENTRY_NAMES = frozenset({"mcp_server.py", "server.py", "main.py", "index.js"})


def _mcp_affinity_corpus(file_map: dict[str, str], spec) -> str:
    """Best available text describing what an MCP server actually does.

    Role affinity (keyword match, then LLM fallback) needs real prose, not
    a launch command: a brand-named security tool's command/args/env names
    rarely share a token with any role id, while its README almost always
    names what it does in plain language. The pack's README (or, failing
    that, its entrypoint source — tool docstrings/registrations are still
    signal) is already captured in ``file_map`` by the installer; this just
    reads it instead of leaving it unused. Falls back to the command text
    only when neither is present.
    """
    for rel, text in sorted(file_map.items()):
        if Path(rel).name.lower() in _README_NAMES:
            return text
    wanted = {str(a) for a in spec.args} | {str(spec.command or "")}
    for rel, text in sorted(file_map.items()):
        name = Path(rel).name
        if name in wanted or name.lower() in _MCP_ENTRY_NAMES:
            return text
    return " ".join(
        (
            spec.command,
            " ".join(spec.args),
            spec.cwd,
            getattr(spec, "url", "") or "",
            " ".join(n for n, _req in spec.env_schema),
        )
    )


def _normalize_tools_filter(raw: Any) -> dict[str, Any] | None:
    """Validate a per-binding tool filter; ``None`` means "no filter"."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("binding tools filter must be an object")
    mode = str(raw.get("mode") or "").strip().lower()
    if mode not in {"include", "exclude"}:
        raise ValueError("binding tools filter mode must be include or exclude")
    values = raw.get("values")
    if not isinstance(values, (list, tuple)):
        raise ValueError("binding tools filter values must be a list")
    cleaned = sorted({str(v).strip() for v in values if str(v or "").strip()})
    if not cleaned:
        # No tool names given is never an intentional "block everything"
        # (include) or a no-op (exclude) — treat it as the operator not
        # having curated anything yet, i.e. unset.
        return None
    return {"mode": mode, "values": cleaned}


# Linear kill-chain order Core 1.3's phase gate compares against — the same
# values as ``KillChainPhase``, declared here once so a gate can be checked
# without importing the Django enum at every call site.
KILL_CHAIN_PHASE_ORDER: tuple[str, ...] = (
    "recon",
    "initial-access",
    "post-exploit",
    "reporting",
)


def phase_at_least(current: str, required: str) -> bool:
    """True when ``current`` has reached (or passed) ``required``.

    An unset/unknown ``required`` is no gate (always true). An unset/unknown
    ``current`` has reached nothing (false whenever a gate is actually set).
    """
    req = (required or "").strip().lower()
    if not req or req not in KILL_CHAIN_PHASE_ORDER:
        return True
    cur = (current or "").strip().lower()
    if cur not in KILL_CHAIN_PHASE_ORDER:
        return False
    return KILL_CHAIN_PHASE_ORDER.index(cur) >= KILL_CHAIN_PHASE_ORDER.index(req)


def _normalize_min_phase(raw: Any) -> str:
    """Validate a per-binding phase gate; ``""`` means "no gate"."""
    value = str(raw or "").strip().lower()
    if not value:
        return ""
    if value not in KILL_CHAIN_PHASE_ORDER:
        raise ValueError(f"binding min_phase must be one of {KILL_CHAIN_PHASE_ORDER}")
    return value


_NO_BINDING = object()


def binding_phase_denial(
    *,
    role_id: str,
    owner_kind: str,
    owner_id: str,
    current_phase: str,
    label: str,
    binding: Any = _NO_BINDING,
) -> str:
    """Governance-framed denial when a role<->owner binding's phase gate isn't met.

    Shared by the MCP-tool seam (``orchestrator/mcp/provision.py``) and the
    skill/binary seam (``orchestrator/skills/runtime/sandbox.py``) so a
    ``min_phase`` gate is enforced identically regardless of owner kind —
    one mechanism, not two. Pass an already-fetched ``binding`` (e.g. a
    caller that also needs it for a tool filter) to skip the lookup here;
    omit it to have this function fetch it itself.
    """
    if binding is _NO_BINDING:
        try:
            rows = PlatformCatalog.shared().list_bindings(
                role_id=role_id, owner_kind=owner_kind, owner_id=owner_id
            )
        except Exception as exc:
            logger.warning(
                "phase-gate binding lookup failed for %s/%s/%s: %s",
                owner_kind,
                owner_id,
                role_id,
                exc,
            )
            return ""
        binding = rows[0] if rows else None
    min_phase = str(getattr(binding, "min_phase", "") or "")
    if not min_phase or phase_at_least(current_phase, min_phase):
        return ""
    from orchestrator.utils.governance import frame_denial

    return frame_denial(
        f"{label} is gated to phase {min_phase!r}; this job's objective is "
        f"at phase {current_phase or '(none)'!r}"
    )


def _bump() -> float:
    global _revision
    _revision = max(_revision + 0.001, time.time())
    return _revision


def _remove_catalog_disk_dir(root: Path, name: str) -> None:
    """Best-effort delete of ``root/name`` — undoes a disk import so a later
    sync (``refresh_stale_skills``/``sync_mcps_from_disk``) does not silently
    re-create what the operator just deleted from the catalog.
    """
    if not name or "/" in name or "\\" in name or ".." in name:
        return
    try:
        if not root.is_dir():
            return
        root_resolved = root.resolve()
        target = (root_resolved / name).resolve()
    except OSError:
        return
    if target.parent != root_resolved or not target.is_dir():
        return
    try:
        shutil.rmtree(target)
        logger.info("removed catalog pack from disk: %s", target)
    except OSError as exc:
        logger.warning("failed to remove catalog pack %s: %s", target, exc)


def _invalidate() -> None:
    _bump()
    try:
        from orchestrator.crew.roles.registry import RoleRegistry
        from orchestrator.skills.registry import SkillRegistry

        SkillRegistry.shared().invalidate()
        RoleRegistry.shared().invalidate()
    except Exception:
        logger.warning("catalog cache invalidate skipped", exc_info=True)


def _hash_payload(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _safe_rel(rel: str) -> str:
    rel_s = str(rel or "").replace("\\", "/").lstrip("/")
    if not rel_s or rel_s.startswith("/") or ".." in rel_s.split("/"):
        raise ValueError(f"unsafe path {rel!r}")
    return rel_s


def _format_candidates(candidates: list[dict[str, Any]]) -> str:
    """``role_id (score)`` list for a review proposal's human-readable rationale."""
    return ", ".join(
        f"{c['role_id']} ({float(c['score']):.2f})"
        for c in sorted(candidates, key=lambda c: -float(c["score"]))
    )


def _payload_mentions(payload: Any, needle: str) -> bool:
    """True when ``needle`` appears as a string value anywhere in ``payload``.

    Proposal payloads nest ids under different keys per kind (``owner_id``,
    ``role_id``, ``skill_id``, ``related_skill_id``, a nested ``role`` dict's
    own ``role_id``, ...). Walking every string value once covers all of
    them without hardcoding each kind's shape.
    """
    if isinstance(payload, str):
        return payload == needle
    if isinstance(payload, dict):
        return any(_payload_mentions(v, needle) for v in payload.values())
    if isinstance(payload, (list, tuple)):
        return any(_payload_mentions(v, needle) for v in payload)
    return False


class PlatformCatalog(SharedServiceBase):
    """CRUD for the platform skill, role, and MCP catalogs."""

    def revision(self) -> float:
        """Stamp registries use to drop a stale in-process cache."""
        try:
            from django.db.models import Max

            from peon.projects.models import (
                CatalogBinding,
                CatalogMcp,
                CatalogSkill,
                EngagementRole,
            )

            stamps: list[float] = []
            for model in (EngagementRole, CatalogSkill, CatalogMcp, CatalogBinding):
                latest = model.objects.aggregate(m=Max("updated_at")).get("m")
                if latest is not None:
                    stamps.append(float(latest.timestamp()))
        except Exception:
            stamps = []
        return max([_revision, *stamps], default=_revision)

    # --- roles -------------------------------------------------------------

    def ensure_seeded(self) -> None:
        """Insert default roles and, once, bootstrap skills/MCPs from disk."""
        self._seed_roles()
        self._import_skill_trees()
        self._import_mcp_packs()

    def _seed_roles(self) -> int:
        from orchestrator.crew.roles.catalog import DEFAULT_ROLES
        from peon.projects.models import EngagementRole

        try:
            if EngagementRole.objects.exists():
                return 0
        except Exception as exc:
            logger.debug("role table unavailable: %s", exc)
            return 0
        created = 0
        try:
            with transaction.atomic():
                for role in DEFAULT_ROLES:
                    _, was = EngagementRole.objects.get_or_create(
                        role_id=role.id,
                        defaults={
                            "kind": role.kind,
                            "aliases": list(role.aliases),
                            "protected": bool(role.protected),
                            "goal": role.goal,
                            "backstory": role.backstory,
                        },
                    )
                    if was:
                        created += 1
        except IntegrityError:
            return 0
        if created:
            _invalidate()
        return created

    def list_roles(self):
        from orchestrator.crew.roles.catalog import DEFAULT_ROLES, RoleDef
        from peon.projects.models import EngagementRole

        try:
            self._seed_roles()
            rows = EngagementRole.objects.all().order_by("role_id")
            return tuple(
                RoleDef(
                    id=str(row.role_id),
                    kind=str(row.kind),
                    aliases=tuple(str(a) for a in (row.aliases or []) if str(a).strip()),
                    protected=bool(row.protected),
                    pending=bool(row.pending),
                    goal=str(row.goal or ""),
                    backstory=str(row.backstory or ""),
                )
                for row in rows
            )
        except Exception as exc:
            logger.debug("list_roles failed: %s", exc)
            return DEFAULT_ROLES

    def get_role(self, role_id: str):
        from orchestrator.crew.roles.catalog import normalize_role_id

        rid = normalize_role_id(role_id)
        if not rid:
            return None
        for row in self.list_roles():
            if row.id == rid or rid in row.aliases:
                return row
        return None

    def upsert_role(
        self,
        *,
        role_id: str,
        kind: str,
        aliases: list[str] | tuple[str, ...] | str = (),
        protected: bool | None = None,
        pending: bool | None = None,
        previous_id: str = "",
        goal: str | None = None,
        backstory: str | None = None,
    ):
        from orchestrator.crew.roles.catalog import (
            BOOKEND_KINDS,
            normalize_role_id,
            role_def_from_mapping,
        )
        from peon.projects.models import EngagementRole

        defn = role_def_from_mapping(
            {
                "id": role_id,
                "kind": kind,
                "aliases": aliases,
                "protected": bool(protected) if protected is not None else False,
            }
        )
        previous = normalize_role_id(previous_id) if previous_id else ""
        if previous == defn.id:
            previous = ""
        if previous:
            old = EngagementRole.objects.filter(role_id=previous).first()
            if old is None:
                raise ValueError(f"unknown role {previous!r}")
            if bool(old.protected) or old.kind in BOOKEND_KINDS:
                raise ValueError(f"role {previous!r} is protected and cannot be renamed")
            if EngagementRole.objects.filter(role_id=defn.id).exists():
                raise ValueError(f"role {defn.id!r} already exists")
        existing = EngagementRole.objects.filter(role_id=defn.id).first()
        if existing is not None:
            current_protected = bool(existing.protected) or existing.kind in BOOKEND_KINDS
            if current_protected and defn.kind != existing.kind:
                raise ValueError(
                    f"role {defn.id!r} is protected; kind cannot change from {existing.kind!r}"
                )
            keep_protected = bool(existing.protected)
            final_kind = existing.kind if current_protected else defn.kind
        else:
            keep_protected = defn.protected
            final_kind = defn.kind

        if protected is not None and not (
            existing and keep_protected and existing.kind in BOOKEND_KINDS
        ):
            keep_protected = bool(protected)
        if final_kind in BOOKEND_KINDS:
            keep_protected = True
            clash = EngagementRole.objects.filter(kind=final_kind).exclude(role_id=defn.id).first()
            if clash is not None:
                raise ValueError(f"kind {final_kind!r} already used by role {clash.role_id!r}")

        self._seed_roles()
        for other in EngagementRole.objects.all().iterator():
            if other.role_id in {defn.id, previous}:
                continue
            other_aliases = tuple(
                str(a).strip().lower() for a in (other.aliases or []) if str(a).strip()
            )
            conflict = {other.role_id, *other_aliases} & {defn.id, *defn.aliases}
            if conflict:
                raise ValueError(
                    f"alias/id conflict with role {other.role_id!r}: {sorted(conflict)}"
                )

        with transaction.atomic():
            from orchestrator.crew.roles.catalog import seed_role_text

            source = existing
            if source is None and previous:
                source = EngagementRole.objects.filter(role_id=previous).first()
            if pending is None:
                pending_flag = bool(source.pending) if source is not None else False
            else:
                pending_flag = bool(pending)
            seeded_goal, seeded_story = seed_role_text(defn.id, final_kind)
            if goal is None:
                goal_text = (source.goal if source is not None else "") or seeded_goal
            else:
                goal_text = str(goal).strip() or seeded_goal
            if backstory is None:
                story_text = (source.backstory if source is not None else "") or seeded_story
            else:
                story_text = str(backstory).strip() or seeded_story
            EngagementRole.objects.update_or_create(
                role_id=defn.id,
                defaults={
                    "kind": final_kind,
                    "aliases": list(defn.aliases),
                    "protected": keep_protected,
                    "pending": pending_flag,
                    "goal": goal_text,
                    "backstory": story_text,
                },
            )
            if previous:
                from peon.projects.models import CatalogBinding, RoleSkillMemory

                CatalogBinding.objects.filter(role_id=previous).update(role_id=defn.id)
                memory = RoleSkillMemory.objects.filter(role_id=previous).first()
                if memory is not None:
                    RoleSkillMemory.objects.update_or_create(
                        role_id=defn.id,
                        defaults={
                            "skills": memory.skills,
                            "counts": memory.counts,
                            "last_source": memory.last_source,
                            "last_project_id": memory.last_project_id,
                        },
                    )
                    memory.delete()
                EngagementRole.objects.filter(role_id=previous).delete()
        _invalidate()

        def _resweep() -> None:
            try:
                from peon.projects.tasks import enqueue_catalog_resweep

                enqueue_catalog_resweep()
            except Exception as exc:
                logger.warning("catalog resweep enqueue failed: %s", exc)

        # Deferred to commit: this call may itself be nested inside a caller's
        # own ``transaction.atomic()`` (e.g. ``apply_proposal``) — enqueueing
        # before that outer transaction commits would let the sweep query a
        # role/binding state that isn't visible yet.
        transaction.on_commit(_resweep)
        return self.get_role(defn.id)

    def confirm_role(self, role_id: str) -> None:
        """Clear a drafted role's ``pending`` flag — "looks fine, no edits needed"."""
        from orchestrator.crew.roles.catalog import normalize_role_id
        from peon.projects.models import EngagementRole

        rid = normalize_role_id(role_id)
        if not rid:
            raise ValueError("invalid role id")
        updated = EngagementRole.objects.filter(role_id=rid).update(pending=False)
        if not updated:
            raise KeyError(f"unknown role {rid!r}")
        _invalidate()

    def resweep_unbound_owners(self, *, limit: int = 50) -> dict[str, Any]:
        """Re-run role resolution for every skill/MCP with zero bindings.

        Enqueued (``peon.projects.tasks.enqueue_catalog_resweep``) whenever a
        role is created or edited — a role that didn't exist before may now
        be exactly what a previously-orphaned item needs; an edited role's
        new goal/backstory may newly resemble one too. Cheap in the steady
        state (no orphans): one anti-join query per owner kind, nothing else.
        ``limit`` bounds one run; any remainder waits for the next trigger.
        """
        from peon.projects.models import CatalogBinding, CatalogMcp, CatalogSkill

        bound_skills = set(
            CatalogBinding.objects.filter(owner_kind=OWNER_SKILL).values_list("owner_id", flat=True)
        )
        bound_mcps = set(
            CatalogBinding.objects.filter(owner_kind=OWNER_MCP).values_list("owner_id", flat=True)
        )
        orphans: list[tuple[str, str]] = [
            (OWNER_SKILL, sid)
            for sid in CatalogSkill.objects.exclude(
                skill_id__in=bound_skills | _roleless_core_skill_ids()
            ).values_list("skill_id", flat=True)
        ] + [
            (OWNER_MCP, mid)
            for mid in CatalogMcp.objects.exclude(mcp_id__in=bound_mcps).values_list(
                "mcp_id", flat=True
            )
        ]
        resolved = 0
        for owner_kind, owner_id in orphans[:limit]:
            try:
                if owner_kind == OWNER_SKILL:
                    rec = self.get_skill(owner_id)
                    if rec is None:
                        continue
                    self._apply_owner_bindings(
                        owner_kind=OWNER_SKILL,
                        owner_id=owner_id,
                        bindings=None,
                        infer_id=owner_id,
                        infer_description=rec.description,
                        infer_body=rec.body,
                        is_new=True,
                    )
                else:
                    mcp_rec = self.get_mcp(owner_id)
                    if mcp_rec is None:
                        continue
                    self._apply_owner_bindings(
                        owner_kind=OWNER_MCP,
                        owner_id=owner_id,
                        bindings=None,
                        infer_id=owner_id,
                        infer_description=mcp_rec.description,
                        infer_body=_mcp_affinity_corpus(mcp_rec.files, mcp_rec),
                        is_new=True,
                    )
                resolved += 1
            except Exception as exc:
                logger.warning("resweep failed for %s %s: %s", owner_kind, owner_id, exc)
        return {
            "scanned": len(orphans),
            "resolved": resolved,
            "remaining": max(0, len(orphans) - limit),
        }

    def delete_role(self, role_id: str) -> None:
        from orchestrator.crew.roles.catalog import FROZEN_ROLE_IDS, normalize_role_id
        from peon.projects.models import EngagementRole

        rid = normalize_role_id(role_id)
        if not rid:
            raise ValueError("invalid role id")
        # Closed freeze list: no engagement role may be deleted from the catalog.
        if rid in FROZEN_ROLE_IDS:
            raise ValueError(f"role {rid!r} is frozen and cannot be deleted")
        row = EngagementRole.objects.filter(role_id=rid).first()
        if row is None:
            raise KeyError(f"unknown role {rid!r}")
        current = self.get_role(rid)
        if current is not None and current.is_protected:
            raise ValueError(f"role {rid!r} is protected and cannot be deleted")
        raise ValueError(
            f"role {rid!r} is outside the frozen catalog and cannot be deleted; "
            "remap or remove bindings instead"
        )

    # --- bindings ----------------------------------------------------------

    def list_bindings(
        self,
        *,
        role_id: str = "",
        owner_kind: str = "",
        owner_id: str = "",
    ) -> tuple[Binding, ...]:
        from peon.projects.models import CatalogBinding

        qs = CatalogBinding.objects.all()
        if role_id:
            qs = qs.filter(role_id=role_id.strip().lower())
        if owner_kind:
            qs = qs.filter(owner_kind=owner_kind.strip().lower())
        if owner_id:
            qs = qs.filter(owner_id=owner_id.strip())
        try:
            rows = qs.order_by("owner_kind", "owner_id", "role_id")
            return tuple(
                Binding(
                    role_id=str(row.role_id),
                    owner_kind=str(row.owner_kind),
                    owner_id=str(row.owner_id),
                    score=float(row.score),
                    lead=bool(row.lead),
                    tools=dict(row.tools) if isinstance(row.tools, dict) else None,
                    min_phase=str(getattr(row, "min_phase", "") or ""),
                )
                for row in rows
            )
        except Exception as exc:
            logger.debug("list_bindings failed: %s", exc)
            return ()

    def replace_bindings(
        self,
        *,
        owner_kind: str,
        owner_id: str,
        bindings: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    ) -> tuple[Binding, ...]:
        """Replace every role mapping for one skill or MCP server."""
        from peon.projects.models import CatalogBinding

        kind = str(owner_kind or "").strip().lower()
        oid = str(owner_id or "").strip()
        if kind not in {OWNER_SKILL, OWNER_MCP}:
            raise ValueError("owner_kind must be skill or mcp")
        if not oid:
            raise ValueError("owner id is required")
        cleaned: list[Binding] = []
        seen: set[str] = set()
        for raw in bindings or ():
            if not isinstance(raw, dict):
                raise ValueError("binding must be an object")
            from orchestrator.crew.roles.catalog import normalize_role_id

            rid = normalize_role_id(raw.get("role_id"))
            if not rid:
                raise ValueError("binding role_id is invalid")
            if rid in seen:
                raise ValueError(f"duplicate binding for role {rid!r}")
            seen.add(rid)
            if self.get_role(rid) is None:
                raise ValueError(f"unknown role {rid!r}")
            score = float(raw.get("score", 1.0))
            if score < 0 or score > 1:
                raise ValueError("score must be between 0 and 1")
            cleaned.append(
                Binding(
                    rid,
                    kind,
                    oid,
                    score,
                    bool(raw.get("lead")),
                    tools=_normalize_tools_filter(raw.get("tools")),
                    min_phase=_normalize_min_phase(raw.get("min_phase")),
                )
            )
        leads = [b for b in cleaned if b.lead]
        if len(leads) > 1:
            raise ValueError("at most one binding may be lead")
        with transaction.atomic():
            CatalogBinding.objects.filter(owner_kind=kind, owner_id=oid).delete()
            CatalogBinding.objects.bulk_create(
                [
                    CatalogBinding(
                        role_id=b.role_id,
                        owner_kind=kind,
                        owner_id=oid,
                        score=b.score,
                        lead=b.lead,
                        tools=b.tools,
                        min_phase=b.min_phase,
                    )
                    for b in cleaned
                ]
            )
        _invalidate()
        return tuple(cleaned)

    def _apply_owner_bindings(
        self,
        *,
        owner_kind: str,
        owner_id: str,
        bindings: list[dict[str, Any]] | None,
        rename_from: str = "",
        infer_id: str = "",
        infer_description: str = "",
        infer_body: str = "",
        infer_tags: tuple[str, ...] = (),
        is_new: bool = True,
    ) -> None:
        """Write explicit bindings, else rename carry, else auto-infer roles.

        ``is_new`` is false for a skill/MCP already in the catalog whose
        bindings happened to be empty or omitted (editor Save without
        touching the bindings section, a second install attempt, …) — such
        a call never auto-infers: re-scoring against *today's* full role
        list, possibly years after the item's own original install, is what
        silently rebound an unrelated MCP to a role that did not even exist
        at its real install time (classifier noise, not a deliberate human
        choice). Only a genuinely new item gets to have auto-infer — or, for
        an MCP specifically, the draft-a-role-and-skill path below — decide
        its role at all.

        ``bindings is None`` or ``[]`` → clear when mappings already exist,
        else auto-infer (but only when ``is_new``; see above).
        A non-empty list always replaces with exactly those rows.
        """
        if bindings:
            self.replace_bindings(owner_kind=owner_kind, owner_id=owner_id, bindings=bindings)
            if is_new and owner_kind == OWNER_SKILL:
                self._flag_skill_overlap(owner_id)
            return
        if bindings is not None:
            existing = self.list_bindings(owner_kind=owner_kind, owner_id=owner_id)
            if existing:
                self.replace_bindings(owner_kind=owner_kind, owner_id=owner_id, bindings=[])
                return
        if rename_from and rename_from != owner_id:
            carried = self.list_bindings(owner_kind=owner_kind, owner_id=rename_from)
            if carried:
                self.replace_bindings(
                    owner_kind=owner_kind,
                    owner_id=owner_id,
                    bindings=[
                        {
                            "role_id": b.role_id,
                            "score": b.score,
                            "lead": b.lead,
                            "tools": b.tools,
                        }
                        for b in carried
                    ],
                )
                return
        if not is_new:
            _invalidate()
            return
        if owner_kind == OWNER_SKILL and owner_id in _roleless_core_skill_ids():
            # Toolsmith's own authoring skills — called by id directly, never
            # dispatched to an engagement role. Deliberately role-less, not
            # an orphan a resweep should keep trying to "fix".
            _invalidate()
            return
        try:
            from orchestrator.skills.affinity import infer_catalog_bindings

            applied, candidates = infer_catalog_bindings(
                owner_id=infer_id or owner_id,
                owner_kind=owner_kind,
                description=infer_description,
                body=infer_body,
                tags=infer_tags,
            )
        except Exception as exc:
            logger.warning("auto-bind failed for %s %s: %s", owner_kind, owner_id, exc)
            applied, candidates = [], []
        if applied:
            try:
                self.replace_bindings(owner_kind=owner_kind, owner_id=owner_id, bindings=applied)
                if owner_kind == OWNER_MCP:
                    lead = next((b for b in applied if b["lead"]), applied[0])
                    self._ensure_mcp_skill(
                        mcp_id=owner_id,
                        description=infer_description,
                        corpus=infer_body,
                        role_id=lead["role_id"],
                    )
                else:
                    self._flag_skill_overlap(owner_id)
                return
            except ValueError as exc:
                logger.warning("auto-bind rejected for %s %s: %s", owner_kind, owner_id, exc)
        self._resolve_role_for_owner(
            owner_kind=owner_kind,
            owner_id=owner_id,
            description=infer_description,
            corpus=infer_body,
            weak_candidates=candidates,
        )
        _invalidate()

    def _resolve_role_for_owner(
        self,
        *,
        owner_kind: str,
        owner_id: str,
        description: str,
        corpus: str,
        weak_candidates: list[dict[str, Any]],
    ) -> None:
        """No confident bind — stage HITL against the frozen role allowlist.

        Called from skill/MCP auto-bind when affinity has no strong match.
        Role invent is disabled: never auto-create EngagementRole rows.
        Schemas: CatalogProposal (mcp_binding|skill_binding), CatalogBinding.
        """
        from orchestrator.crew.roles.catalog import DEFAULT_ROLES, FROZEN_ROLE_IDS
        from peon.projects.models import CatalogProposal

        already_pending = CatalogProposal.objects.filter(
            status="pending",
            kind__in=("mcp_binding", "skill_binding"),
            action="create",
            target_id=owner_id,
        ).exists()
        if already_pending:
            return
        candidates = list(weak_candidates or [])
        if not candidates:
            candidates = [
                {"role_id": r.id, "score": 0.0, "lead": False}
                for r in DEFAULT_ROLES
                if r.id in FROZEN_ROLE_IDS and r.kind == "technical"
            ]
        self._stage_binding_review(
            owner_kind=owner_kind,
            owner_id=owner_id,
            candidates=candidates,
            description=description,
            corpus=corpus,
        )

    def _ensure_mcp_skill(
        self, *, mcp_id: str, description: str, corpus: str, role_id: str
    ) -> None:
        """A newly bound MCP still gets its own skill even when it bound to an
        *existing* role (e.g. BloodHound MCP confidently matching the
        ``ad-attack-expert`` role AdStrike's install already created) — the
        generic MCP tool-call surface works without one, but a server-specific
        workflow (ordering, OPSEC, how its own setup/credential call works) is
        the whole value of a skill. No-op if this MCP already has one.

        The companion skill is evaluated as a genuine new-skill event
        (``bindings=None``) rather than force-bound to ``role_id`` — most
        often it lands on that same role (the drafted text names it
        explicitly, below, so the unbiased affinity match favours it), but it
        can also land elsewhere or get flagged for review, same as any skill.
        """
        from peon.projects.models import CatalogSkill

        if CatalogSkill.objects.filter(skill_id=mcp_id).exists():
            return
        draft = self._draft_role(
            owner_kind=OWNER_MCP, owner_id=mcp_id, description=description, corpus=corpus
        )
        if draft is None:
            return
        from orchestrator.crew.roles.draft import render_skill_md

        role = self.get_role(role_id)
        hint = f"\n\nPrimary existing role for this server: `{role_id}`" + (
            f" — {role.backstory}" if role is not None and role.backstory else ""
        )
        draft = {**draft, "skill_body": draft["skill_body"] + hint}
        try:
            self.upsert_skill(
                skill_md=render_skill_md(mcp_id, draft),
                skill_id=mcp_id,
                bindings=None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("mcp skill create failed for %s: %s", mcp_id, exc)

    @staticmethod
    def _draft_role(
        *, owner_kind: str, owner_id: str, description: str, corpus: str
    ) -> dict[str, Any] | None:
        """``draft_role``, logged and swallowed on failure (never fatal)."""
        from orchestrator.crew.roles.draft import draft_role

        try:
            return draft_role(
                owner_kind=owner_kind, owner_id=owner_id, description=description, corpus=corpus
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("role draft failed for %s %s: %s", owner_kind, owner_id, exc)
            return None

    def _stage_binding_review(
        self,
        *,
        owner_kind: str,
        owner_id: str,
        candidates: list[dict[str, Any]],
        description: str = "",
        corpus: str = "",
    ) -> None:
        """Flag a skill/MCP server with no confident auto-bind for human review.

        ``description``/``corpus`` stay in the payload for operators reviewing
        the match context. ``kind`` carries ``owner_kind`` so the right Catalog
        section (and its "Pending" nav badge) picks the review up — it used to
        be hardcoded to ``"mcp_binding"`` regardless of owner, which left every
        skill weak-match review invisible under the Skills tab.
        """
        from peon.projects.models import CatalogProposal

        label = "MCP server" if owner_kind == OWNER_MCP else "skill"
        kind = "mcp_binding" if owner_kind == OWNER_MCP else "skill_binding"
        try:
            already_pending = CatalogProposal.objects.filter(
                status="pending", kind=kind, action="create", target_id=owner_id
            ).exists()
            if already_pending:
                return
            names = _format_candidates(candidates)
            CatalogProposal.objects.create(
                kind=kind,
                action="create",
                target_id=owner_id,
                title=f"No confident role match for {label}: {owner_id}",
                # The review card only renders title/rationale (no payload UI),
                # so the candidates have to be readable here, not just in
                # payload — approving blind would defeat the review step.
                rationale=(
                    "Auto-binding scored every role below the confidence "
                    f"threshold. Closest unconfirmed matches: {names}. "
                    "Approve to bind all of them, or reject and bind manually "
                    "from this item's own page to a frozen catalog role."
                ),
                payload={
                    "owner_kind": owner_kind,
                    "owner_id": owner_id,
                    "candidates": candidates,
                    "description": description,
                    "corpus": corpus,
                },
            )
        except Exception as exc:
            logger.warning("mcp binding review stage failed for %s: %s", owner_id, exc)

    def recheck_bindings_on_update(
        self,
        *,
        owner_kind: str,
        owner_id: str,
        description: str = "",
        body: str = "",
        tags: tuple[str, ...] = (),
    ) -> None:
        """Re-run role-affinity scoring after an edit changes an *existing*
        skill/MCP's content — never writes a binding itself, only flags for
        human review (the same ``skill_binding``/``mcp_binding`` queue a
        fresh weak-match install already uses) when none of its current
        role(s) still score confidently against the edited content.

        Deliberately does not call (or change) ``_apply_owner_bindings``:
        that function's own contract is "never auto-infer for an existing
        item" (see its docstring — re-scoring an existing item against
        today's full role list is exactly what silently rebinds something
        on classifier noise). This is a separate, read-only check sitting
        beside it — same scanning primitive (``infer_catalog_bindings``),
        same review queue, but the outcome is always "ask a human", never
        "change the binding".
        """
        current = self.list_bindings(owner_kind=owner_kind, owner_id=owner_id)
        if not current:
            return  # nothing bound yet; a fresh-install check already owns that case
        from orchestrator.skills.affinity import infer_catalog_bindings

        try:
            applied, candidates = infer_catalog_bindings(
                owner_id=owner_id,
                owner_kind=owner_kind,
                description=description,
                body=body,
                tags=tags,
            )
        except Exception as exc:
            logger.warning(
                "post-edit binding recheck failed for %s %s: %s", owner_kind, owner_id, exc
            )
            return
        still_confident = {str(a["role_id"]) for a in applied}
        bound_role_ids = {b.role_id for b in current}
        if bound_role_ids & still_confident:
            return  # at least one current binding still scores confidently — nothing to flag
        # candidates is the honest result against the edited content — pass
        # it through as-is (even empty) rather than substituting the old,
        # now-stale scores the current binding was made with; those would
        # misreport what the edited content actually scores today.
        self._stage_binding_review(
            owner_kind=owner_kind,
            owner_id=owner_id,
            candidates=candidates,
            description=description,
            corpus=body,
        )

    def _flag_skill_overlap(self, skill_id: str) -> None:
        """Stage review when a newly bound skill reads like a role-mate's twin.

        Called once, right after a skill's bindings are first set. Compares
        only against skills sharing a role with ``skill_id`` — a skill with
        no siblings yet (its own brand-new role, or the only skill on an
        existing one) has nothing to compare against and this is a no-op.
        """
        from orchestrator.skills.overlap import find_overlap
        from peon.projects.models import CatalogProposal

        rec = self.get_skill(skill_id)
        if rec is None:
            return
        own_role_ids = {
            b.role_id for b in self.list_bindings(owner_kind=OWNER_SKILL, owner_id=skill_id)
        }
        sibling_ids = {
            b.owner_id
            for role_id in own_role_ids
            for b in self.list_bindings(owner_kind=OWNER_SKILL, role_id=role_id)
            if b.owner_id != skill_id
        }
        if not sibling_ids:
            return
        siblings = {
            sib.skill_id: f"{sib.description}\n{sib.body}"
            for sib in (self.get_skill(sid) for sid in sibling_ids)
            if sib is not None
        }
        try:
            match = find_overlap(skill_id, f"{rec.description}\n{rec.body}", siblings)
        except Exception as exc:
            logger.warning("overlap check failed for skill %s: %s", skill_id, exc)
            return
        if match is None:
            return
        already_pending = CatalogProposal.objects.filter(
            status="pending", kind="skill_overlap", target_id=skill_id
        ).exists()
        if already_pending:
            return
        CatalogProposal.objects.create(
            kind="skill_overlap",
            action="create",
            target_id=skill_id,
            title=f"Possible overlap: {skill_id} vs {match.related_skill_id}",
            rationale=match.reason,
            payload={"skill_id": skill_id, "related_skill_id": match.related_skill_id},
        )

    # --- skill relations -----------------------------------------------------

    def list_skill_relations(self, *, skill_id: str = "") -> list[dict[str, Any]]:
        """Every declared skill<->skill relation, optionally for one skill (either side)."""
        from django.db.models import Q

        from peon.projects.models import CatalogSkillRelation

        qs = CatalogSkillRelation.objects.all()
        if skill_id:
            qs = qs.filter(Q(skill_id=skill_id) | Q(related_skill_id=skill_id))
        return [
            {
                "id": str(row.id),
                "skill_id": row.skill_id,
                "related_skill_id": row.related_skill_id,
                "kind": row.kind,
                "note": row.note,
                "conditions": row.conditions or "",
            }
            for row in qs.order_by("skill_id", "related_skill_id")
        ]

    def set_skill_relation(
        self,
        *,
        skill_id: str,
        related_skill_id: str,
        kind: str,
        note: str = "",
        conditions: str = "",
    ) -> dict[str, Any]:
        """Create or update one relation row. The only write path a human uses directly."""
        from peon.projects.models import CatalogSkillRelation, SkillRelationKind

        sid = str(skill_id or "").strip()
        rid = str(related_skill_id or "").strip()
        if not sid or not rid or sid == rid:
            raise ValueError("skill_id and related_skill_id must both be set and different")
        if kind not in SkillRelationKind.values:
            raise ValueError(f"unknown relation kind {kind!r}")
        if self.get_skill(sid) is None or self.get_skill(rid) is None:
            raise ValueError("both skills must already exist in the catalog")
        row, _ = CatalogSkillRelation.objects.update_or_create(
            skill_id=sid,
            related_skill_id=rid,
            kind=kind,
            defaults={"note": note, "conditions": (conditions or "").strip()},
        )
        return {
            "id": str(row.id),
            "skill_id": sid,
            "related_skill_id": rid,
            "kind": kind,
            "note": row.note,
            "conditions": row.conditions or "",
        }

    def delete_skill_relation(self, relation_id: str) -> None:
        from peon.projects.models import CatalogSkillRelation

        deleted, _ = CatalogSkillRelation.objects.filter(pk=relation_id).delete()
        if not deleted:
            raise KeyError(f"unknown relation {relation_id!r}")

    def _purge_dangling_references(self, *, owner_kind: str, owner_id: str) -> None:
        """Delete everything else in the catalog that names ``owner_id``.

        Called by every delete path (role, skill, MCP) so "removed" means
        gone everywhere — bindings, declared skill relations, any pending
        review that was about this exact item, and learned role→skill usage
        memory — not just the row itself. One generic sweep instead of each
        delete method guessing which other tables might mention it.
        """
        from django.db.models import Q

        from peon.projects.models import (
            CatalogBinding,
            CatalogProposal,
            CatalogSkillRelation,
            RoleSkillMemory,
        )

        if owner_kind == OWNER_ROLE:
            CatalogBinding.objects.filter(role_id=owner_id).delete()
            RoleSkillMemory.objects.filter(role_id=owner_id).delete()
        else:
            CatalogBinding.objects.filter(owner_kind=owner_kind, owner_id=owner_id).delete()
            # role_skills_prompt_block() feeds this straight into planner/
            # router/spawn prompts as "previously used" — a stale id there
            # is a dead option the model might still try to bind.
            for row in RoleSkillMemory.objects.all():
                skills = [s for s in (row.skills or []) if s != owner_id]
                if len(skills) == len(row.skills or []):
                    continue
                counts = dict(row.counts or {})
                counts.pop(owner_id, None)
                row.skills = skills
                row.counts = counts
                row.save(update_fields=["skills", "counts", "updated_at"])
        if owner_kind == OWNER_SKILL:
            CatalogSkillRelation.objects.filter(
                Q(skill_id=owner_id) | Q(related_skill_id=owner_id)
            ).delete()
        for row in CatalogProposal.objects.filter(status="pending"):
            if row.target_id == owner_id or _payload_mentions(row.payload, owner_id):
                row.delete()

    # --- skills ------------------------------------------------------------

    def list_skills(self) -> tuple[SkillRecord, ...]:
        from peon.projects.models import CatalogSkill

        try:
            self._import_skill_trees()
            rows = CatalogSkill.objects.all().order_by("skill_id")
        except Exception as exc:
            logger.debug("list_skills failed: %s", exc)
            return ()
        bindings = self.list_bindings(owner_kind=OWNER_SKILL)
        by_owner: dict[str, list[Binding]] = {}
        for binding in bindings:
            by_owner.setdefault(binding.owner_id, []).append(binding)
        return tuple(
            SkillRecord(
                skill_id=str(row.skill_id),
                provider=str(row.provider or "peon"),
                description=str(row.description or ""),
                body=str(row.body or ""),
                manifest=dict(row.manifest or {}),
                setup=dict(row.setup) if isinstance(row.setup, dict) else None,
                files={},
                bindings=tuple(by_owner.get(str(row.skill_id), ())),
            )
            for row in rows
        )

    def get_skill(self, skill_id: str) -> SkillRecord | None:
        from peon.projects.models import CatalogFile, CatalogSkill

        sid = str(skill_id or "").strip()
        if not sid:
            return None
        try:
            row = CatalogSkill.objects.filter(skill_id=sid).first()
        except Exception as exc:
            logger.debug("get_skill failed: %s", exc)
            return None
        if row is None and "/" not in sid:
            row = CatalogSkill.objects.filter(skill_id=sid).first()
        if row is None:
            return None
        files = {
            str(item.path): str(item.body or "")
            for item in CatalogFile.objects.filter(owner_kind=OWNER_SKILL, owner_id=row.skill_id)
        }
        bindings = self.list_bindings(owner_kind=OWNER_SKILL, owner_id=row.skill_id)
        return SkillRecord(
            skill_id=str(row.skill_id),
            provider=str(row.provider or "peon"),
            description=str(row.description or ""),
            body=str(row.body or ""),
            manifest=dict(row.manifest or {}),
            setup=dict(row.setup) if isinstance(row.setup, dict) else None,
            files=files,
            bindings=bindings,
        )

    def upsert_skill(
        self,
        *,
        skill_md: str,
        files: dict[str, str] | None = None,
        skill_id: str = "",
        provider: str = "peon",
        bindings: list[dict[str, Any]] | None = None,
        keep_bindings: bool = False,
        from_disk: bool = False,
        only_if_pristine: bool = False,
    ) -> SkillRecord:
        """Create/update one catalog skill.

        ``from_disk`` stamps the row with the digest it was imported at
        (``manifest["_imported_hash"]``). A row without that stamp, or whose
        content has moved on from it, was authored/edited in the catalog.
        ``only_if_pristine`` makes a disk refresh leave such rows alone.
        """
        from orchestrator.skills.manifest import ParseProfile, SkillParseError, parse_skill_manifest
        from orchestrator.skills.providers.base import PEON_PROVIDER, SkillId
        from peon.projects.models import CatalogFile, CatalogSkill

        md = str(skill_md or "").strip()
        if not md:
            raise ValueError("skill_md is required")
        file_map: dict[str, str] = {}
        for key, value in (files or {}).items():
            rel = _safe_rel(str(key))
            if rel == "SKILL.md" or str(value).startswith("<<binary omitted:"):
                continue
            file_map[rel] = str(value)
        hinted = str(skill_id or "").strip()
        parsed_id = SkillId.parse(hinted) if hinted else None
        # Catalog rows have no on-disk folder; use the frontmatter name as the
        # synthetic directory so PEON profile validation can match.
        from orchestrator.skills.manifest import skill_name_from_markdown

        directory = (
            skill_name_from_markdown(md)
            or (parsed_id.name if parsed_id is not None else "")
            or (hinted or "skill")
        )
        profile = (
            ParseProfile.CLAUDE_CODE
            if (parsed_id is not None and parsed_id.provider != PEON_PROVIDER)
            or provider != PEON_PROVIDER
            else ParseProfile.PEON
        )
        try:
            manifest = parse_skill_manifest(
                Path(directory) / "SKILL.md",
                profile=profile,
                text=md if md.endswith("\n") else md + "\n",
            )
        except SkillParseError as exc:
            raise ValueError(exc.message) from exc
        public = manifest.command_name
        prov = PEON_PROVIDER if profile == ParseProfile.PEON else "claude"
        if prov != PEON_PROVIDER:
            public = f"{prov}/{manifest.command_name}"
        rename_from = ""
        if hinted and SkillId.parse(hinted) is not None:
            want = SkillId.parse(hinted)
            assert want is not None
            if want.name != manifest.command_name:
                if CatalogSkill.objects.filter(skill_id=public).exists():
                    raise ValueError(f"skill {public!r} already exists")
                rename_from = want.public
            else:
                public = want.public
                prov = want.provider
        payload = _manifest_payload(manifest)
        payload["_source"] = md if md.endswith("\n") else md + "\n"
        setup = _setup_from_files(file_map)
        post = _setup_post(manifest.metadata)
        if setup is not None and post:
            setup = {**setup, "post": post}
        digest = _hash_payload({"md": md, "files": file_map, "provider": prov})
        current = CatalogSkill.objects.filter(skill_id=public).first()
        existed = current is not None
        content_changed = existed and current.content_hash != digest
        if only_if_pristine and current is not None:
            if current.content_hash == digest:
                if from_disk and not (current.manifest or {}).get("_imported_hash"):
                    # Adopt: identical to disk, so it is a pristine import.
                    current.manifest = {
                        **(current.manifest or {}),
                        "_imported_hash": digest,
                    }
                    current.save(update_fields=["manifest"])
                return self.get_skill(public)  # already current
            stamp = (current.manifest or {}).get("_imported_hash")
            if not stamp or stamp != current.content_hash:
                logger.warning(
                    "skill %s differs from disk but was edited in the catalog; "
                    "not overwriting (use 'sync from disk' to force)",
                    public,
                )
                return self.get_skill(public)
        if from_disk:
            payload["_imported_hash"] = digest
        with transaction.atomic():
            CatalogSkill.objects.update_or_create(
                skill_id=public,
                defaults={
                    "provider": prov,
                    "description": manifest.description,
                    "body": manifest.body,
                    "manifest": payload,
                    "setup": setup,
                    "content_hash": digest,
                },
            )
            CatalogFile.objects.filter(owner_kind=OWNER_SKILL, owner_id=public).delete()
            CatalogFile.objects.bulk_create(
                [
                    CatalogFile(
                        owner_kind=OWNER_SKILL,
                        owner_id=public,
                        path=rel,
                        body=text,
                    )
                    for rel, text in sorted(file_map.items())
                ]
            )
        if keep_bindings and existed and not rename_from:
            _invalidate()
        else:
            self._apply_owner_bindings(
                owner_kind=OWNER_SKILL,
                owner_id=public,
                bindings=bindings,
                rename_from=rename_from if rename_from != public else "",
                infer_id=public,
                infer_description=manifest.description,
                infer_body=manifest.body,
                infer_tags=tuple(manifest.tags or ()),
                is_new=not existed,
            )
        if content_changed and not rename_from:
            # An edit to an already-catalogued skill — not a create/rename,
            # and the content (not just metadata) genuinely changed. The
            # binding decision above never re-scores an existing item (by
            # design); this only checks whether it still *should* match and
            # flags a review if not. See recheck_bindings_on_update.
            self.recheck_bindings_on_update(
                owner_kind=OWNER_SKILL,
                owner_id=public,
                description=manifest.description,
                body=manifest.body,
                tags=tuple(manifest.tags or ()),
            )
        if rename_from and rename_from != public:
            self.delete_skill(rename_from)
        rec = self.get_skill(public)
        if rec is None:
            raise RuntimeError(f"skill {public!r} missing after upsert")
        return rec

    def delete_skill(self, skill_id: str) -> None:
        from orchestrator.skills.providers.base import SkillId
        from peon.projects.models import CatalogFile, CatalogSkill

        sid = str(skill_id or "").strip()
        row = CatalogSkill.objects.filter(skill_id=sid).first()
        if row is None:
            raise KeyError(f"unknown skill {sid!r}")
        if sid in CORE_SKILL_IDS:
            raise ValueError(f"skill {sid!r} is a platform-core skill and cannot be deleted")
        parsed = SkillId.parse(sid)
        bare_name = parsed.name if parsed is not None else sid
        with transaction.atomic():
            CatalogFile.objects.filter(owner_kind=OWNER_SKILL, owner_id=sid).delete()
            self._purge_dangling_references(owner_kind=OWNER_SKILL, owner_id=sid)
            row.delete()
        _invalidate()
        # Catalog row is gone; also drop the pack it was imported from so the
        # next disk sync (job_run/objectives call refresh_stale_skills before
        # every run) does not silently re-import what was just deleted. The
        # catalog's own ``provider`` field is a content-sniffed flavor (a
        # Claude-style skill can still physically live under SKILLS_DIR, see
        # infer_skill_kind()) — not reliable for picking a root, so try every
        # configured skill root rather than branching on it.
        for root, _mode in self._skill_disk_roots():
            _remove_catalog_disk_dir(root, bare_name)

    # --- mcp ---------------------------------------------------------------

    def list_mcps(self) -> tuple[McpRecord, ...]:
        from peon.projects.models import CatalogMcp

        try:
            rows = list(CatalogMcp.objects.all().order_by("mcp_id"))
        except Exception as exc:
            logger.debug("list_mcps failed: %s", exc)
            return ()
        bindings = self.list_bindings(owner_kind=OWNER_MCP)
        by_owner: dict[str, list[Binding]] = {}
        for binding in bindings:
            by_owner.setdefault(binding.owner_id, []).append(binding)
        return tuple(_mcp_from_row(row, tuple(by_owner.get(row.mcp_id, ())), {}) for row in rows)

    def get_mcp(self, mcp_id: str) -> McpRecord | None:
        from peon.projects.models import CatalogFile, CatalogMcp

        mid = str(mcp_id or "").strip().lower()
        if not mid:
            return None
        try:
            row = CatalogMcp.objects.filter(mcp_id=mid).first()
        except Exception as exc:
            logger.debug("get_mcp failed: %s", exc)
            return None
        if row is None:
            return None
        files = {
            str(item.path): str(item.body or "")
            for item in CatalogFile.objects.filter(owner_kind=OWNER_MCP, owner_id=mid)
        }
        bindings = self.list_bindings(owner_kind=OWNER_MCP, owner_id=mid)
        return _mcp_from_row(row, bindings, files)

    def upsert_mcp(
        self,
        document: dict[str, Any] | str,
        *,
        files: dict[str, str] | None = None,
        bindings: list[dict[str, Any]] | None = None,
        enabled: bool | None = None,
        rename_from: str = "",
        keep_bindings: bool = False,
    ) -> McpRecord:
        from orchestrator.mcp.parse import parse_mcp_document
        from peon.projects.models import CatalogFile, CatalogMcp

        specs = parse_mcp_document(document)
        if len(specs) != 1:
            raise ValueError("upsert_mcp accepts exactly one server")
        spec = specs[0]
        previous = str(rename_from or "").strip().lower()
        if previous in {"", "__new__", spec.mcp_id}:
            previous = ""
        if previous:
            if not CatalogMcp.objects.filter(mcp_id=previous).exists():
                raise ValueError(f"unknown mcp server {previous!r}")
            if CatalogMcp.objects.filter(mcp_id=spec.mcp_id).exists():
                raise ValueError(f"mcp server {spec.mcp_id!r} already exists")
        existing = CatalogMcp.objects.filter(mcp_id=spec.mcp_id).first()
        existed = existing is not None
        keep_enabled = bool(existing.enabled) if existing is not None else True
        if enabled is not None:
            keep_enabled = bool(enabled)
        # None = leave files; {} = clear; dict = replace.
        replace_files = files is not None
        if replace_files:
            file_map = {_safe_rel(k): str(v) for k, v in files.items()}
        else:
            file_map = {
                str(item.path): str(item.body or "")
                for item in CatalogFile.objects.filter(owner_kind=OWNER_MCP, owner_id=spec.mcp_id)
            }
        digest = _hash_payload(
            {
                "command": spec.command,
                "args": list(spec.args),
                "cwd": spec.cwd,
                "install": spec.install,
                "env_schema": spec.env_schema_full,
                "reference_hosts": list(spec.reference_hosts),
                "url": spec.url,
                "files": file_map,
            }
        )
        schema = list(spec.env_schema_full)
        with transaction.atomic():
            CatalogMcp.objects.update_or_create(
                mcp_id=spec.mcp_id,
                defaults={
                    "description": spec.description,
                    "transport": spec.transport,
                    "command": spec.command,
                    "args": list(spec.args),
                    "cwd": spec.cwd,
                    "install": spec.install,
                    "env_schema": schema,
                    "reference_hosts": list(spec.reference_hosts),
                    "url": spec.url,
                    "enabled": keep_enabled,
                    "content_hash": digest,
                },
            )
            if replace_files:
                CatalogFile.objects.filter(owner_kind=OWNER_MCP, owner_id=spec.mcp_id).delete()
                if file_map:
                    CatalogFile.objects.bulk_create(
                        [
                            CatalogFile(
                                owner_kind=OWNER_MCP,
                                owner_id=spec.mcp_id,
                                path=rel,
                                body=text,
                            )
                            for rel, text in sorted(file_map.items())
                        ]
                    )
        if keep_bindings and existed and not previous:
            _invalidate()
        else:
            self._apply_owner_bindings(
                owner_kind=OWNER_MCP,
                owner_id=spec.mcp_id,
                bindings=bindings,
                rename_from=previous,
                infer_id=spec.mcp_id,
                infer_description=spec.description,
                infer_body=_mcp_affinity_corpus(file_map, spec),
                infer_tags=(),
                is_new=not existed,
            )
        if previous:
            self.delete_mcp(previous)
        rec = self.get_mcp(spec.mcp_id)
        if rec is None:
            raise RuntimeError(f"mcp {spec.mcp_id!r} missing after upsert")
        return rec

    def save_mcp_document(
        self,
        document: dict[str, Any] | str,
        *,
        files: dict[str, str] | None = None,
        bindings: list[dict[str, Any]] | None = None,
        enabled: bool | None = None,
        rename_from: str = "",
    ) -> tuple[McpRecord, ...]:
        """Create or replace one server, or update every entry in an mcpServers map."""
        from orchestrator.mcp.parse import parse_mcp_document

        specs = parse_mcp_document(document)
        if len(specs) == 1:
            return (
                self.upsert_mcp(
                    document,
                    files=files,
                    bindings=bindings,
                    enabled=enabled,
                    rename_from=rename_from,
                ),
            )
        if files or bindings or rename_from:
            raise ValueError(
                "an mcpServers map updates each named server; "
                "save files, bindings, and renames on one server"
            )
        saved: list[McpRecord] = []
        for spec in specs:
            body: dict[str, Any] = {
                "name": spec.mcp_id,
                "description": spec.description,
                "transport": spec.transport,
                "command": spec.command,
                "args": list(spec.args),
                "cwd": spec.cwd,
                "env_schema": list(spec.env_schema_full),
            }
            if spec.url:
                body["url"] = spec.url
            if spec.install:
                body["install"] = spec.install
            saved.append(self.upsert_mcp(body, enabled=enabled))
        return tuple(saved)

    def delete_mcp(self, mcp_id: str) -> None:
        from django.conf import settings

        from peon.projects.models import CatalogFile, CatalogMcp

        mid = str(mcp_id or "").strip().lower()
        row = CatalogMcp.objects.filter(mcp_id=mid).first()
        if row is None:
            raise KeyError(f"unknown mcp server {mid!r}")
        with transaction.atomic():
            CatalogFile.objects.filter(owner_kind=OWNER_MCP, owner_id=mid).delete()
            self._purge_dangling_references(owner_kind=OWNER_MCP, owner_id=mid)
            row.delete()
        _invalidate()
        # Every CatalogMcp row is imported from mcps/<id>/ (server code lives
        # on disk, unlike a skill) — drop it so a later sync_mcps_from_disk()
        # does not silently re-create the server just deleted.
        _remove_catalog_disk_dir(Path(getattr(settings, "MCPS_DIR", "") or ""), mid)

    # --- bootstrap / disk sync ---------------------------------------------

    def _skill_disk_roots(self) -> list[tuple[Path, str]]:
        from django.conf import settings

        roots: list[tuple[Path, str]] = []
        skills = Path(getattr(settings, "SKILLS_DIR", "") or "")
        if skills.is_dir():
            roots.append((skills, "infer"))
        for extra in getattr(settings, "SKILLS_EXTRA_DIRS", ()) or ():
            path = Path(extra)
            if path.is_dir():
                roots.append((path, "claude"))
        return roots

    def _import_skill_trees(self) -> int:
        from peon.projects.models import CatalogSkill

        try:
            if CatalogSkill.objects.exists():
                return 0
        except Exception as exc:
            logger.debug("skill table unavailable: %s", exc)
            return 0
        self._seed_roles()
        count = 0
        for root, mode in self._skill_disk_roots():
            count += len(self._import_skill_root(root, mode, keep_bindings=False))
        if count:
            logger.info("bootstrapped %s skills into the platform catalog", count)
        return count

    def sync_skills_from_disk(self) -> dict[str, list[str]]:
        """Re-read ``skills/`` into the catalog. Preserves existing role bindings."""
        from peon.projects.models import CatalogSkill

        self._seed_roles()
        before = {
            str(row.skill_id): str(row.content_hash or "") for row in CatalogSkill.objects.all()
        }
        seen: set[str] = set()
        for root, mode in self._skill_disk_roots():
            for skill_id in self._import_skill_root(root, mode, keep_bindings=True):
                seen.add(skill_id)
        after = {
            str(row.skill_id): str(row.content_hash or "")
            for row in CatalogSkill.objects.filter(skill_id__in=seen)
        }
        added = sorted(sid for sid in seen if sid not in before)
        modified = sorted(
            sid for sid in seen if sid in before and before[sid] != after.get(sid, "")
        )
        # Disk is additive: catalog-only skills (UI-authored) are not removed.
        return {"added": added, "removed": [], "modified": modified}

    def _skills_disk_fingerprint(self) -> str:
        """Cheap change detector for the on-disk skill trees (path/mtime/size)."""
        h = hashlib.sha256()
        for root, mode in self._skill_disk_roots():
            h.update(f"{root}:{mode}".encode())
            for path in sorted(root.rglob("*")):
                try:
                    if path.is_file() and not any(
                        part.startswith(".") or part == "__pycache__"
                        for part in path.relative_to(root).parts
                    ):
                        st = path.stat()
                        h.update(
                            f"{path.relative_to(root).as_posix()}:{st.st_mtime_ns}:{st.st_size}".encode()
                        )
                except OSError:
                    continue
        return h.hexdigest()

    def refresh_stale_skills(self, *, force: bool = False) -> dict[str, list[str]]:
        """Bring catalog skills up to date with ``skills/`` before work runs.

        Only rows that are pristine imports are refreshed; skills edited in the
        catalog (UI) or authored there are never overwritten. A fingerprint of
        the disk trees makes the common no-change case a cheap stat walk.
        """
        global _SKILLS_FINGERPRINT
        try:
            fingerprint = self._skills_disk_fingerprint()
        except Exception as exc:  # noqa: BLE001
            logger.debug("skill fingerprint failed: %s", exc)
            return {"added": [], "removed": [], "modified": []}
        if not force and fingerprint == _SKILLS_FINGERPRINT:
            return {"added": [], "removed": [], "modified": []}
        from peon.projects.models import CatalogSkill

        before = {str(r.skill_id): str(r.content_hash or "") for r in CatalogSkill.objects.all()}
        seen: set[str] = set()
        for root, mode in self._skill_disk_roots():
            seen.update(
                self._import_skill_root(root, mode, keep_bindings=True, only_if_pristine=True)
            )
        after = {
            str(r.skill_id): str(r.content_hash or "")
            for r in CatalogSkill.objects.filter(skill_id__in=seen)
        }
        _SKILLS_FINGERPRINT = fingerprint
        added = sorted(sid for sid in seen if sid not in before)
        modified = sorted(
            sid for sid in seen if sid in before and before[sid] != after.get(sid, "")
        )
        if added or modified:
            logger.info("skills refreshed from disk: added=%s modified=%s", added, modified)
            _invalidate()
        return {"added": added, "removed": [], "modified": modified}

    def reference_hosts(self) -> frozenset[str]:
        """Hosts enabled MCP servers declare as reference infrastructure."""
        from peon.projects.models import CatalogMcp

        try:
            rows = CatalogMcp.objects.filter(enabled=True).values_list("reference_hosts", flat=True)
            return frozenset(
                str(h).strip().lower() for hosts in rows for h in (hosts or ()) if str(h).strip()
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("reference_hosts unavailable: %s", exc)
            return frozenset()

    def _import_mcp_packs(self) -> int:
        """Bootstrap ``mcps/`` into an empty catalog (first boot)."""
        from peon.projects.models import CatalogMcp

        try:
            if CatalogMcp.objects.exists():
                return 0
        except Exception as exc:
            logger.debug("mcp table unavailable: %s", exc)
            return 0
        result = self.sync_mcps_from_disk()
        added = len(result.get("added") or [])
        if added:
            logger.info("bootstrapped %s MCP servers into the platform catalog", added)
        return added

    def sync_mcps_from_disk(self) -> dict[str, list[str]]:
        """Re-read ``mcps/`` packs into the catalog. Preserves role bindings."""
        from django.conf import settings

        from peon.projects.models import CatalogMcp

        root = Path(getattr(settings, "MCPS_DIR", "") or "")
        if not root.is_dir():
            return {"added": [], "removed": [], "modified": []}
        before = {str(row.mcp_id): str(row.content_hash or "") for row in CatalogMcp.objects.all()}
        from orchestrator.mcp.install import refresh_mcp_root

        ids = refresh_mcp_root(root, keep_bindings=True)
        after = {
            str(row.mcp_id): str(row.content_hash or "")
            for row in CatalogMcp.objects.filter(mcp_id__in=ids)
        }
        seen = set(ids)
        added = sorted(mid for mid in seen if mid not in before)
        modified = sorted(
            mid for mid in seen if mid in before and before[mid] != after.get(mid, "")
        )
        return {"added": added, "removed": [], "modified": modified}

    def _import_skill_root(
        self,
        root: Path,
        mode: str,
        *,
        keep_bindings: bool,
        only_if_pristine: bool = False,
    ) -> list[str]:
        from orchestrator.skills.providers.base import CLAUDE_PROVIDER, PEON_PROVIDER
        from orchestrator.skills.registry_scan import discover_skill_candidates

        imported: list[str] = []
        default = PEON_PROVIDER if mode == "infer" else CLAUDE_PROVIDER
        source = "native" if mode == "infer" else "imported"
        try:
            candidates = discover_skill_candidates(root, source=source, skip_helpers=True)
        except Exception as exc:
            logger.warning("skill bootstrap scan failed for %s: %s", root, exc)
            return []
        for candidate in candidates:
            default_provider = default if mode == "infer" else CLAUDE_PROVIDER
            try:
                rec = self.upsert_skill_from_candidate(
                    candidate,
                    default_provider=default_provider,
                    keep_bindings=keep_bindings,
                    only_if_pristine=only_if_pristine,
                )
            except Exception as exc:
                logger.warning("skipped skill %s: %s", candidate.root, exc)
                continue
            if rec is not None:
                imported.append(rec.skill_id)
        return imported

    def upsert_skill_from_candidate(
        self,
        candidate,
        *,
        default_provider: str,
        keep_bindings: bool = False,
        only_if_pristine: bool = False,
        from_disk: bool = True,
    ) -> SkillRecord | None:
        """Upsert one ``SkillCandidate`` (from ``discover_skill_candidates``).

        Shared by the disk bootstrap/sync scan and the marketplace importer —
        the actual rules (namespace disambiguation, kind inference, file
        collection, affinity-derived bindings) live here exactly once.
        """
        from orchestrator.skills.common import apply_skill_namespace, parse_frontmatter_yaml
        from orchestrator.skills.kind import catalog_id_for, infer_skill_kind

        text = candidate.manifest.read_text(encoding="utf-8")
        if candidate.namespace:
            # upsert_skill derives the catalog id from SKILL.md's own
            # frontmatter `name:`, not from a skill_id hint (a mismatched
            # hint is treated as a rename request, not an override) — so
            # disambiguating two bundles' same-named skill means rewriting
            # the frontmatter itself before upserting.
            text = apply_skill_namespace(text, candidate.namespace)
        parsed_fm = parse_frontmatter_yaml(text)
        frontmatter_name = (
            str(parsed_fm[0].get("name") or "").strip().lower() if parsed_fm else ""
        ) or candidate.root.name
        kind = infer_skill_kind(candidate.root, default_provider=default_provider)
        files: dict[str, str] = {}
        for path in sorted(candidate.root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(candidate.root).as_posix()
            if rel == "SKILL.md" or path.name.startswith("."):
                continue
            try:
                files[rel] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        # No bindings hint from disk — a freshly-imported skill goes through
        # the same unified auto-infer/draft-a-role pipeline as any other new
        # skill (``_apply_owner_bindings``), never a sidecar file.
        return self.upsert_skill(
            skill_md=text,
            files=files,
            skill_id=catalog_id_for(frontmatter_name, kind),
            provider=kind.provider,
            bindings=None,
            keep_bindings=keep_bindings,
            from_disk=from_disk,
            only_if_pristine=only_if_pristine,
        )

    def ingest_imported_skill(self, name: str) -> str:
        """Load a pack from ``skills/<name>`` into the platform catalog.

        A thin wrapper over ``upsert_skill_from_candidate`` — file collection,
        affinity-derived bindings, and kind inference all live there once.
        """
        from django.conf import settings

        from orchestrator.skills.common import find_manifest
        from orchestrator.skills.providers.base import CLAUDE_PROVIDER
        from orchestrator.skills.registry_scan import SkillCandidate

        slug = str(name or "").strip().lower()
        if not slug:
            raise ValueError("skill name is required")
        skills_root = Path(getattr(settings, "SKILLS_DIR", "") or "")
        root = skills_root / slug
        if not root.is_dir():
            raise ValueError(f"skill directory missing: {skills_root / slug}")
        manifest = find_manifest(root)
        if manifest is None:
            raise ValueError(f"{root} has no SKILL.md")
        candidate = SkillCandidate(root=root, manifest=manifest, source="imported")
        # Installed / Lab packs default to claude unless signals say otherwise.
        rec = self.upsert_skill_from_candidate(candidate, default_provider=CLAUDE_PROVIDER)
        return rec.skill_id


def _manifest_payload(manifest) -> dict[str, Any]:
    return {
        "tools": list(manifest.allowed_tools),
        "allow_binaries": list(manifest.allow_binaries),
        "tags": list(manifest.tags),
        "compatibility": manifest.compatibility,
        "license": manifest.license,
        "when_to_use": manifest.when_to_use,
        "argument_hint": manifest.argument_hint,
        "arguments": list(manifest.arguments),
        "disallowed_tools": list(manifest.disallowed_tools),
        "disable_model_invocation": manifest.disable_model_invocation,
        "user_invocable": manifest.user_invocable,
        "model": manifest.model,
        "context": manifest.context,
        "agent": manifest.agent,
        "background": manifest.background,
        "paths": list(manifest.paths),
        "shell": manifest.shell,
        "hooks": manifest.hooks,
        "profile": manifest.profile,
    }


def _setup_post(metadata: dict[str, str] | None) -> list[str]:
    """Commands from SKILL.md ``metadata: {setup-post: ...}`` (JSON list or lines)."""
    raw = str((metadata or {}).get("setup-post") or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = []
        items = [str(x) for x in parsed] if isinstance(parsed, list) else []
    else:
        items = raw.splitlines()
    return [c.strip() for c in items if c.strip() and "\x00" not in c][:8]


def _setup_from_files(files: dict[str, str]) -> dict[str, str] | None:
    for kind, name in (
        ("pip", "requirements.txt"),
        ("pip", "pyproject.toml"),
        ("npm", "package.json"),
    ):
        if name in files:
            return {"kind": kind, "cwd": ".", "manifest": name}
    return None


_ENV_TYPES = frozenset({"string", "secret", "boolean", "number", "choice", "credential"})
_SECRET_HINT = re.compile(
    r"(token|password|secret|api_?key|authorization|access_token|refresh_token)", re.I
)


def _mcp_from_row(row, bindings: tuple[Binding, ...], files: dict[str, str]) -> McpRecord:
    schema_raw = row.env_schema or []
    schema: list[tuple[str, bool]] = []
    schema_full: list[dict[str, Any]] = []
    if isinstance(schema_raw, list):
        for item in schema_raw:
            if isinstance(item, str) and item.strip():
                name, required, extra = item.strip(), True, {}
            elif isinstance(item, dict) and str(item.get("name") or "").strip():
                name = str(item["name"]).strip()
                required = bool(item.get("required", True))
                extra = item
            else:
                continue
            schema.append((name, required))
            etype = str(extra.get("type") or "").strip().lower()
            if etype not in _ENV_TYPES:
                etype = "secret" if _SECRET_HINT.search(name) else "string"
            schema_full.append(
                {
                    "name": name,
                    "required": required,
                    "type": etype,
                    "description": str(extra.get("description") or ""),
                    "default": "" if extra.get("default") is None else str(extra.get("default")),
                    "choices": [str(c) for c in (extra.get("choices") or [])],
                    "credential_part": str(extra.get("credential_part") or "secret"),
                }
            )
    install = dict(row.install) if isinstance(row.install, dict) else None
    args = tuple(str(a) for a in (row.args or []))
    return McpRecord(
        mcp_id=str(row.mcp_id),
        description=str(row.description or ""),
        transport=str(row.transport or "stdio"),
        command=str(row.command or ""),
        args=args,
        cwd=str(getattr(row, "cwd", "") or ""),
        install=install,
        env_schema=tuple(schema),
        env_schema_full=tuple(schema_full),
        url=str(getattr(row, "url", "") or ""),
        enabled=bool(row.enabled),
        files=files,
        bindings=bindings,
        reference_hosts=tuple(str(h) for h in (getattr(row, "reference_hosts", None) or ())),
    )
