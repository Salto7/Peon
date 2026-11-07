"""Per-project MCP env configuration (values never live in the catalog)."""

from __future__ import annotations

import logging
from typing import Any

from orchestrator.utils.strings import is_env_name, normalize_env_map

logger = logging.getLogger(__name__)


def _engagement_role_ids(project_id: str) -> set[str]:
    """Role ids planned or run on this engagement (objectives + jobs)."""
    roles: set[str] = set()
    try:
        from peon.projects.db import ensure_project_database
        from peon.projects.models import Job, Objective

        alias = ensure_project_database(project_id)
        for rid in (
            Objective.objects.using(alias)
            .exclude(role_id="")
            .values_list("role_id", flat=True)
            .distinct()
        ):
            t = str(rid or "").strip().lower()
            if t:
                roles.add(t)
        for role_ids in (
            Job.objects.using(alias)
            .exclude(role_ids=[])
            .values_list("role_ids", flat=True)[:40]
        ):
            for rid in list(role_ids or [])[:8]:
                t = str(rid or "").strip().lower()
                if t:
                    roles.add(t)
    except Exception as exc:
        logger.debug("engagement role ids failed for %s: %s", project_id, exc)
    return roles


def list_project_mcp_configs(
    project_id: object,
    *,
    configured_only: bool = False,
    relevant_only: bool = False,
) -> list[dict[str, Any]]:
    """Enabled catalog MCP servers with this project's stored env values.

    war-room MCP pane), MCP save handlers.
    ``relevant_only``: already configured **or** bound to a role this
    engagement uses — avoids chicken-and-egg (configured_only alone hid the
    form that creates ProjectMcpConfig).
    """
    from peon.projects.db import ensure_project_database
    from peon.projects.models import ProjectMcpConfig
    from peon.projects.platform_catalog import PlatformCatalog

    pid = str(project_id or "").strip()
    if not pid:
        return []
    catalog = PlatformCatalog.shared()
    stored_by: dict[str, dict[str, str]] = {}
    try:
        alias = ensure_project_database(pid)
        for row in ProjectMcpConfig.objects.using(alias).filter(project_id=pid):
            stored_by[str(row.mcp_id)] = normalize_env_map(row.env)
    except Exception as exc:
        logger.warning("list project mcp configs failed for %s: %s", pid, exc)

    role_ids = _engagement_role_ids(pid) if relevant_only else set()
    out: list[dict[str, Any]] = []
    for rec in catalog.list_mcps():
        if not rec.enabled:
            continue
        if configured_only and rec.mcp_id not in stored_by:
            continue
        if relevant_only and rec.mcp_id not in stored_by:
            bound = {str(b.role_id or "").strip().lower() for b in rec.bindings}
            if not (bound & role_ids):
                continue
        stored = stored_by.get(rec.mcp_id) or {}
        # Secret values never go back to the browser; the form shows "set".
        fields = [
            {
                "name": f["name"],
                "required": bool(f.get("required")),
                "type": f.get("type") or "string",
                "description": f.get("description") or "",
                "default": f.get("default") or "",
                "choices": list(f.get("choices") or []),
                "value": (
                    ""
                    if f.get("type") == "secret"
                    else stored.get(f["name"], f.get("default") or "")
                ),
                "secret": f.get("type") == "secret",
                "credential": f.get("type") == "credential",
                "is_set": bool(stored.get(f["name"])),
            }
            for f in rec.env_schema_full
        ]
        missing = [
            f["name"]
            for f in rec.env_schema_full
            if f.get("required") and not stored.get(f["name"])
        ]
        out.append(
            {
                "mcp_id": rec.mcp_id,
                "description": rec.description or "",
                "command": rec.command,
                "args": list(rec.args or ()),
                "roles": [b.role_id for b in rec.bindings],
                "fields": fields,
                "missing": missing,
                "ready": not missing,
                # True only when ProjectMcpConfig exists — role binding alone
                "configured": rec.mcp_id in stored_by,
            }
        )
    return out


def mcp_nav_visible(configs: list[dict[str, Any]]) -> bool:
    """War-room MCP pane: configured servers, or role-bound ones needing secrets.

    bound to ``recon`` with no required env (e.g. corp-osint) must not appear
    any mcps, mcp configuration should not be visible".
    """
    return any(bool(c.get("configured")) or bool(c.get("missing")) for c in configs or [])


def _stored_env(alias: str, project_id: str, mcp_id: str) -> dict[str, str]:
    from peon.projects.models import ProjectMcpConfig

    row = (
        ProjectMcpConfig.objects.using(alias)
        .filter(project_id=project_id, mcp_id=mcp_id)
        .first()
    )
    raw = row.env if row is not None else {}
    return normalize_env_map(raw)


def save_project_mcp_env(
    project_id: object,
    mcp_id: str,
    env: dict[str, Any],
    *,
    clear: set[str] | frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Replace stored env for one MCP server. Only catalog schema names are kept.

    A blank *secret* field keeps its stored value (the form never receives
    it); name it in ``clear`` to remove it.
    """
    from peon.projects.db import require_project_db
    from peon.projects.models import ProjectMcpConfig
    from peon.projects.platform_catalog import PlatformCatalog
    from peon.projects.secrets_crypto import encrypt_secret, retain_unset_secrets

    mid = str(mcp_id or "").strip().lower()
    if not mid:
        raise ValueError("project and mcp_id are required")
    pid, alias = require_project_db(project_id)
    rec = PlatformCatalog.shared().get_mcp(mid)
    if rec is None or not rec.enabled:
        raise ValueError(f"unknown or disabled MCP server {mid!r}")
    fields_by_name = {f["name"]: f for f in rec.env_schema_full}
    allowed = set(fields_by_name)
    cleaned: dict[str, str] = {}
    for key, value in (env or {}).items():
        name = str(key or "").strip()
        if not name or name not in allowed:
            continue
        if not is_env_name(name):
            raise ValueError(f"invalid env name {name!r}")
        text = "" if value is None else str(value).strip()
        etype = fields_by_name[name].get("type") or "string"
        if etype == "boolean":
            if text:
                text = "true" if text.lower() in {"true", "1", "on", "yes"} else "false"
        elif etype == "number" and text:
            try:
                float(text)
            except ValueError:
                raise ValueError(f"{name} must be a number") from None
        elif etype == "choice" and text:
            choices = fields_by_name[name].get("choices") or []
            if choices and text not in choices:
                raise ValueError(f"{name} must be one of {', '.join(choices)}")
        if text and etype == "secret":
            text = encrypt_secret(text)
        if text:
            cleaned[name] = text

    stored = _stored_env(alias, pid, mid)
    secret_keys = {n for n, f in fields_by_name.items() if f.get("type") == "secret"}
    cleaned = retain_unset_secrets(cleaned, stored, secret_keys=secret_keys, clear=clear)

    qs = ProjectMcpConfig.objects.using(alias).filter(project_id=pid, mcp_id=mid)
    if not cleaned:
        qs.delete()
        return {}
    ProjectMcpConfig.objects.using(alias).update_or_create(
        project_id=pid,
        mcp_id=mid,
        defaults={"env": cleaned},
    )
    return cleaned


def save_project_mcp_from_post(project_id: object, post) -> int:
    """Parse ``env__<mcp_id>__<NAME>`` form fields. Returns servers updated."""
    buckets: dict[str, dict[str, str]] = {}
    clears: dict[str, set[str]] = {}
    for key in post:
        prefix, _, rest = str(key or "").partition("__")
        if prefix not in {"env", "clear"}:
            continue
        mid, _, name = rest.partition("__")
        mid, name = mid.strip().lower(), name.strip()
        if not mid or not name:
            continue
        if prefix == "env":
            buckets.setdefault(mid, {})[name] = str(post.get(key) or "")
        else:
            clears.setdefault(mid, set()).add(name)
    # Also accept explicit empty buckets for servers that only cleared fields.
    for mid in post.getlist("mcp_id"):
        buckets.setdefault(str(mid).strip().lower(), {})
    updated = 0
    for mid, env in buckets.items():
        if not mid:
            continue
        save_project_mcp_env(project_id, mid, env, clear=clears.get(mid, set()))
        updated += 1
    return updated
