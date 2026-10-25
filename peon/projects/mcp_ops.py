"""Per-project MCP env configuration (values never live in the catalog)."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def list_project_mcp_configs(project_id: object) -> list[dict[str, Any]]:
    """Enabled catalog MCP servers with this project's stored env values."""
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
            raw = row.env if isinstance(row.env, dict) else {}
            stored_by[str(row.mcp_id)] = {
                str(k): str(v)
                for k, v in raw.items()
                if v is not None and str(v).strip()
            }
    except Exception as exc:
        logger.warning("list project mcp configs failed for %s: %s", pid, exc)

    out: list[dict[str, Any]] = []
    for rec in catalog.list_mcps():
        if not rec.enabled:
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
            }
        )
    return out


def _stored_env(alias: str, project_id: str, mcp_id: str) -> dict[str, str]:
    from peon.projects.models import ProjectMcpConfig

    row = (
        ProjectMcpConfig.objects.using(alias)
        .filter(project_id=project_id, mcp_id=mcp_id)
        .first()
    )
    raw = row.env if row is not None and isinstance(row.env, dict) else {}
    return {str(k): str(v) for k, v in raw.items() if v is not None and str(v).strip()}


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
    from peon.projects.db import ensure_project_database
    from peon.projects.models import Project, ProjectMcpConfig
    from peon.projects.platform_catalog import PlatformCatalog

    pid = str(project_id or "").strip()
    mid = str(mcp_id or "").strip().lower()
    if not pid or not mid:
        raise ValueError("project and mcp_id are required")
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
        if not _ENV_NAME.match(name):
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
        if text:
            cleaned[name] = text

    alias = ensure_project_database(pid)
    if not Project.objects.filter(pk=pid).exists():
        raise ValueError(f"unknown project {pid!r}")

    stored = _stored_env(alias, pid, mid)
    for name in allowed:
        is_secret = fields_by_name[name].get("type") == "secret"
        if is_secret and name not in cleaned and name not in clear and stored.get(name):
            cleaned[name] = stored[name]

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
