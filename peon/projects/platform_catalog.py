"""Platform catalog: the only application access to catalog tables.

Roles, skills, MCP server specs, file bodies, and role mappings live in
``workspaces/platform/db.sqlite3``. Callers use this module. They do not query
those models directly.

``skills/`` and ``skills_imported/`` are a one-time bootstrap when the skill
table is empty. After that the database is the registry.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.db import IntegrityError, transaction

logger = logging.getLogger(__name__)

OWNER_SKILL = "skill"
OWNER_MCP = "mcp"
_AFFINITY_THRESHOLD = 0.5
_revision: float = 0.0


@dataclass(frozen=True)
class Binding:
    role_id: str
    owner_kind: str
    owner_id: str
    score: float = 1.0
    lead: bool = False


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


def _bump() -> float:
    global _revision
    _revision = max(_revision + 0.001, time.time())
    return _revision


def _invalidate() -> None:
    _bump()
    try:
        from orchestrator.crew.roles.registry import RoleRegistry
        from orchestrator.skills.registry import SkillRegistry

        SkillRegistry.shared().invalidate()
        RoleRegistry.shared().invalidate()
    except Exception:
        logger.debug("catalog cache invalidate skipped", exc_info=True)


def _hash_payload(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _safe_rel(rel: str) -> str:
    rel_s = str(rel or "").replace("\\", "/").lstrip("/")
    if not rel_s or rel_s.startswith("/") or ".." in rel_s.split("/"):
        raise ValueError(f"unsafe path {rel!r}")
    return rel_s


class PlatformCatalog:
    """CRUD for the platform skill, role, and MCP catalogs."""

    _shared: "PlatformCatalog | None" = None

    @classmethod
    def shared(cls) -> "PlatformCatalog":
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

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
        """Insert default roles and, once, bootstrap skills from disk."""
        self._seed_roles()
        self._import_skill_trees()

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
                    _, was =                     EngagementRole.objects.get_or_create(
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
                    aliases=tuple(
                        str(a) for a in (row.aliases or []) if str(a).strip()
                    ),
                    protected=bool(row.protected),
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
                    f"role {defn.id!r} is protected; kind cannot change from "
                    f"{existing.kind!r}"
                )
            keep_protected = bool(existing.protected)
            final_kind = existing.kind if current_protected else defn.kind
        else:
            keep_protected = defn.protected
            final_kind = defn.kind

        if protected is not None and not (existing and keep_protected and existing.kind in BOOKEND_KINDS):
            keep_protected = bool(protected)
        if final_kind in BOOKEND_KINDS:
            keep_protected = True
            clash = (
                EngagementRole.objects.filter(kind=final_kind)
                .exclude(role_id=defn.id)
                .first()
            )
            if clash is not None:
                raise ValueError(
                    f"kind {final_kind!r} already used by role {clash.role_id!r}"
                )

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
            seeded_goal, seeded_story = seed_role_text(defn.id, final_kind)
            if goal is None:
                goal_text = (source.goal if source is not None else "") or seeded_goal
            else:
                goal_text = str(goal).strip() or seeded_goal
            if backstory is None:
                story_text = (
                    source.backstory if source is not None else ""
                ) or seeded_story
            else:
                story_text = str(backstory).strip() or seeded_story
            EngagementRole.objects.update_or_create(
                role_id=defn.id,
                defaults={
                    "kind": final_kind,
                    "aliases": list(defn.aliases),
                    "protected": keep_protected,
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
        return self.get_role(defn.id)

    def delete_role(self, role_id: str) -> None:
        from orchestrator.crew.roles.catalog import normalize_role_id

        from peon.projects.models import CatalogBinding, EngagementRole

        rid = normalize_role_id(role_id)
        if not rid:
            raise ValueError("invalid role id")
        row = EngagementRole.objects.filter(role_id=rid).first()
        if row is None:
            raise KeyError(f"unknown role {rid!r}")
        current = self.get_role(rid)
        if current is not None and current.is_protected:
            raise ValueError(f"role {rid!r} is protected and cannot be deleted")
        with transaction.atomic():
            CatalogBinding.objects.filter(role_id=rid).delete()
            row.delete()
        _invalidate()

    def roles_for_lab(self) -> list[dict[str, Any]]:
        from orchestrator.crew.roles.registry import RoleRegistry

        registry = RoleRegistry.shared()
        out: list[dict[str, Any]] = []
        for defn in self.list_roles():
            spec = registry.get(defn.id)
            out.append(
                {
                    "id": defn.id,
                    "kind": defn.kind,
                    "aliases": list(defn.aliases),
                    "protected": defn.is_protected,
                    "skills": list(spec.skills or ()) if spec else [],
                }
            )
        return out

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
                )
                for row in rows
            )
        except Exception as exc:
            logger.debug("list_bindings failed: %s", exc)
            return ()

    def upsert_binding(
        self,
        *,
        role_id: str,
        owner_kind: str,
        owner_id: str,
        score: float = 1.0,
        lead: bool = False,
    ) -> Binding:
        from orchestrator.crew.roles.catalog import normalize_role_id

        from peon.projects.models import CatalogBinding

        rid = normalize_role_id(role_id)
        kind = str(owner_kind or "").strip().lower()
        oid = str(owner_id or "").strip()
        if not rid:
            raise ValueError("invalid role id")
        if kind not in {OWNER_SKILL, OWNER_MCP}:
            raise ValueError("owner_kind must be skill or mcp")
        if not oid:
            raise ValueError("owner id is required")
        if self.get_role(rid) is None:
            raise ValueError(f"unknown role {rid!r}")
        if kind == OWNER_SKILL and self.get_skill(oid) is None:
            raise ValueError(f"unknown skill {oid!r}")
        if kind == OWNER_MCP and self.get_mcp(oid) is None:
            raise ValueError(f"unknown mcp server {oid!r}")
        score_f = float(score)
        if score_f < 0 or score_f > 1:
            raise ValueError("score must be between 0 and 1")
        CatalogBinding.objects.update_or_create(
            role_id=rid,
            owner_kind=kind,
            owner_id=oid,
            defaults={"score": score_f, "lead": bool(lead)},
        )
        _invalidate()
        return Binding(rid, kind, oid, score_f, bool(lead))

    def delete_binding(
        self, *, role_id: str, owner_kind: str, owner_id: str
    ) -> None:
        from orchestrator.crew.roles.catalog import normalize_role_id

        from peon.projects.models import CatalogBinding

        rid = normalize_role_id(role_id)
        deleted, _ = CatalogBinding.objects.filter(
            role_id=rid,
            owner_kind=str(owner_kind or "").strip().lower(),
            owner_id=str(owner_id or "").strip(),
        ).delete()
        if not deleted:
            raise KeyError("binding not found")
        _invalidate()

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
                Binding(rid, kind, oid, score, bool(raw.get("lead")))
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
                    )
                    for b in cleaned
                ]
            )
        _invalidate()
        return tuple(cleaned)

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
            for item in CatalogFile.objects.filter(
                owner_kind=OWNER_SKILL, owner_id=row.skill_id
            )
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
    ) -> SkillRecord:
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
        # Catalog rows have no directory. Validate against the manifest name
        # so create and rename are not rejected as a folder mismatch.
        named = re.search(r"(?m)^name:\s*([A-Za-z0-9][A-Za-z0-9-]*)\s*$", md)
        directory = (
            named.group(1).lower()
            if named
            else (parsed_id.name if parsed_id is not None else (hinted or "skill"))
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
        digest = _hash_payload({"md": md, "files": file_map, "provider": prov})
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
            CatalogFile.objects.filter(
                owner_kind=OWNER_SKILL, owner_id=public
            ).delete()
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
        if bindings is not None:
            self.replace_bindings(
                owner_kind=OWNER_SKILL, owner_id=public, bindings=bindings
            )
        elif rename_from and rename_from != public:
            carried = self.list_bindings(owner_kind=OWNER_SKILL, owner_id=rename_from)
            if carried:
                self.replace_bindings(
                    owner_kind=OWNER_SKILL,
                    owner_id=public,
                    bindings=[
                        {
                            "role_id": b.role_id,
                            "score": b.score,
                            "lead": b.lead,
                        }
                        for b in carried
                    ],
                )
            else:
                _invalidate()
        else:
            _invalidate()
        if rename_from and rename_from != public:
            self.delete_skill(rename_from)
        rec = self.get_skill(public)
        if rec is None:
            raise RuntimeError(f"skill {public!r} missing after upsert")
        return rec

    def delete_skill(self, skill_id: str) -> None:
        from peon.projects.models import CatalogBinding, CatalogFile, CatalogSkill

        sid = str(skill_id or "").strip()
        row = CatalogSkill.objects.filter(skill_id=sid).first()
        if row is None:
            raise KeyError(f"unknown skill {sid!r}")
        with transaction.atomic():
            CatalogFile.objects.filter(owner_kind=OWNER_SKILL, owner_id=sid).delete()
            CatalogBinding.objects.filter(owner_kind=OWNER_SKILL, owner_id=sid).delete()
            row.delete()
        _invalidate()

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
        file_map = {_safe_rel(k): str(v) for k, v in (files or {}).items()}
        existing = CatalogMcp.objects.filter(mcp_id=spec.mcp_id).first()
        keep_enabled = bool(existing.enabled) if existing is not None else True
        if enabled is not None:
            keep_enabled = bool(enabled)
        digest = _hash_payload(
            {
                "command": spec.command,
                "args": list(spec.args),
                "cwd": spec.cwd,
                "install": spec.install,
                "env_schema": spec.env_schema,
                "files": file_map,
            }
        )
        schema = [{"name": name, "required": required} for name, required in spec.env_schema]
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
                    "enabled": keep_enabled,
                    "content_hash": digest,
                },
            )
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
        if bindings is not None:
            self.replace_bindings(
                owner_kind=OWNER_MCP, owner_id=spec.mcp_id, bindings=bindings
            )
        elif previous:
            carried = self.list_bindings(owner_kind=OWNER_MCP, owner_id=previous)
            if carried:
                self.replace_bindings(
                    owner_kind=OWNER_MCP,
                    owner_id=spec.mcp_id,
                    bindings=[
                        {
                            "role_id": b.role_id,
                            "score": b.score,
                            "lead": b.lead,
                        }
                        for b in carried
                    ],
                )
            else:
                _invalidate()
        else:
            _invalidate()
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
                "env_schema": [
                    {"name": name, "required": required}
                    for name, required in spec.env_schema
                ],
            }
            if spec.install:
                body["install"] = spec.install
            saved.append(self.upsert_mcp(body, enabled=enabled))
        return tuple(saved)

    def delete_mcp(self, mcp_id: str) -> None:
        from peon.projects.models import CatalogBinding, CatalogFile, CatalogMcp

        mid = str(mcp_id or "").strip().lower()
        row = CatalogMcp.objects.filter(mcp_id=mid).first()
        if row is None:
            raise KeyError(f"unknown mcp server {mid!r}")
        with transaction.atomic():
            CatalogFile.objects.filter(owner_kind=OWNER_MCP, owner_id=mid).delete()
            CatalogBinding.objects.filter(owner_kind=OWNER_MCP, owner_id=mid).delete()
            row.delete()
        _invalidate()

    # --- bootstrap ---------------------------------------------------------

    def _import_skill_trees(self) -> int:
        from django.conf import settings

        from peon.projects.models import CatalogSkill

        try:
            if CatalogSkill.objects.exists():
                return 0
        except Exception as exc:
            logger.debug("skill table unavailable: %s", exc)
            return 0
        roots: list[tuple[Path, str]] = []
        skills = Path(getattr(settings, "SKILLS_DIR", "") or "")
        imported = Path(getattr(settings, "SKILLS_IMPORTED_DIR", "") or "")
        self._seed_roles()
        if skills.is_dir():
            roots.append((skills, "peon"))
        if imported.is_dir() and imported.resolve() != skills.resolve():
            roots.append((imported, "claude"))
        for extra in getattr(settings, "SKILLS_EXTRA_DIRS", ()) or ():
            path = Path(extra)
            if path.is_dir():
                roots.append((path, "claude"))
        count = 0
        for root, provider in roots:
            count += self._import_skill_root(root, provider)
        if count:
            logger.info("bootstrapped %s skills into the platform catalog", count)
        return count

    def _import_skill_root(self, root: Path, provider: str) -> int:
        from orchestrator.skills.affinity import read_affinity
        from orchestrator.skills.registry_scan import discover_skill_candidates

        count = 0
        source = "native" if provider == "peon" else "imported"
        try:
            candidates = discover_skill_candidates(
                root, source=source, skip_helpers=True
            )
        except Exception as exc:
            logger.warning("skill bootstrap scan failed for %s: %s", root, exc)
            return 0
        for candidate in candidates:
            try:
                text = candidate.manifest.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("unreadable skill %s: %s", candidate.manifest, exc)
                continue
            files: dict[str, str] = {}
            for path in sorted(candidate.root.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(candidate.root).as_posix()
                if rel in {"SKILL.md", "peon.affinity.yaml"} or path.name.startswith("."):
                    continue
                try:
                    files[rel] = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
            try:
                affinity = read_affinity(candidate.root)
            except Exception:
                affinity = None
            binding_rows = []
            if affinity is not None:
                for binding in affinity.bindings:
                    if binding.score < _AFFINITY_THRESHOLD:
                        continue
                    role = self.get_role(binding.role_id)
                    if role is None:
                        continue
                    binding_rows.append(
                        {
                            "role_id": role.id,
                            "score": binding.score,
                            "lead": binding.lead,
                        }
                    )
            try:
                self.upsert_skill(
                    skill_md=text,
                    files=files,
                    skill_id=candidate.root.name if provider == "peon" else f"claude/{candidate.root.name}",
                    provider=provider,
                    bindings=binding_rows,
                )
            except Exception as exc:
                logger.warning("skipped skill %s: %s", candidate.root, exc)
                continue
            count += 1
        return count


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


def _setup_from_files(files: dict[str, str]) -> dict[str, str] | None:
    for kind, name in (
        ("pip", "requirements.txt"),
        ("pip", "pyproject.toml"),
        ("npm", "package.json"),
    ):
        if name in files:
            return {"kind": kind, "cwd": ".", "manifest": name}
    return None


def _mcp_from_row(row, bindings: tuple[Binding, ...], files: dict[str, str]) -> McpRecord:
    schema_raw = row.env_schema or []
    schema: list[tuple[str, bool]] = []
    if isinstance(schema_raw, list):
        for item in schema_raw:
            if isinstance(item, str) and item.strip():
                schema.append((item.strip(), True))
            elif isinstance(item, dict) and str(item.get("name") or "").strip():
                schema.append(
                    (str(item["name"]).strip(), bool(item.get("required", True)))
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
        enabled=bool(row.enabled),
        files=files,
        bindings=bindings,
    )
