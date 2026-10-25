"""Project credential ("key store") CRUD. Every role in a project can use these.

Mirrors the ``ProjectMcpConfig`` pattern: secret values are written once and
never read back by the browser. ``get_credential_secret`` is for in-sandbox
agent use and MCP env resolution only — never for an HTTP response body.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from peon.projects.secrets_crypto import SecretDecryptError, decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CredentialSecret:
    name: str
    kind: str
    username: str
    domain: str
    secret: str

    def as_env(self, part: str = "secret") -> str:
        """``secret`` (default) | ``username`` | ``domain`` | ``full`` (``domain\\user:secret``)."""
        if part == "username":
            return self.username
        if part == "domain":
            return self.domain
        if part == "full":
            user = f"{self.domain}\\{self.username}" if self.domain else self.username
            return f"{user}:{self.secret}" if user else self.secret
        return self.secret


def _alias(project_id: str):
    from peon.projects.db import ensure_project_database

    return ensure_project_database(project_id)


def list_credentials(project_id: object) -> list[dict[str, Any]]:
    """Masked rows for UI/agent listing — never the decrypted secret."""
    from peon.projects.models import ProjectCredential

    pid = str(project_id or "").strip()
    if not pid:
        return []
    alias = _alias(pid)
    rows = ProjectCredential.objects.using(alias).filter(project_id=pid).order_by("name")
    return [
        {
            "name": row.name,
            "kind": row.kind,
            "username": row.username,
            "domain": row.domain,
            "notes": row.notes,
            "source": row.source,
            "is_set": bool(row.secret_enc),
            "updated_at": row.updated_at,
        }
        for row in rows
    ]


def set_credential(
    project_id: object,
    name: str,
    *,
    kind: str = "password",
    username: str = "",
    domain: str = "",
    secret: str = "",
    notes: str = "",
    source: str = "provided",
) -> dict[str, Any]:
    """Create or update one credential. A blank ``secret`` keeps the stored value."""
    from peon.projects.models import CredentialKind, Project, ProjectCredential

    pid = str(project_id or "").strip()
    key = (name or "").strip()
    if not pid or not key:
        raise ValueError("project and name are required")
    if not Project.objects.filter(pk=pid).exists():
        raise ValueError("unknown project")
    kind_norm = (kind or "password").strip().lower()
    if kind_norm not in CredentialKind.values:
        kind_norm = CredentialKind.OTHER
    alias = _alias(pid)
    row, created = ProjectCredential.objects.using(alias).get_or_create(
        project_id=pid, name=key, defaults={"kind": kind_norm}
    )
    row.kind = kind_norm
    row.username = (username or "").strip()
    row.domain = (domain or "").strip()
    row.notes = (notes or "").strip()
    row.source = (source or "provided").strip().lower()[:32]
    if secret:
        row.secret_enc = encrypt_secret(secret)
    row.save(using=alias)
    return {"name": row.name, "created": created}


def delete_credential(project_id: object, name: str) -> bool:
    from peon.projects.models import ProjectCredential

    pid = str(project_id or "").strip()
    key = (name or "").strip()
    if not pid or not key:
        return False
    alias = _alias(pid)
    deleted, _ = ProjectCredential.objects.using(alias).filter(
        project_id=pid, name=key
    ).delete()
    return bool(deleted)


def get_credential_secret(project_id: object, name: str) -> CredentialSecret | None:
    """Decrypted credential for in-sandbox agent/MCP use. ``None`` if unknown."""
    from peon.projects.models import ProjectCredential

    pid = str(project_id or "").strip()
    key = (name or "").strip()
    if not pid or not key:
        return None
    alias = _alias(pid)
    row = (
        ProjectCredential.objects.using(alias)
        .filter(project_id=pid, name=key)
        .first()
    )
    if row is None:
        return None
    try:
        secret = decrypt_secret(row.secret_enc)
    except SecretDecryptError as exc:
        logger.warning("credential %s/%s undecryptable: %s", pid, key, exc)
        secret = ""
    return CredentialSecret(
        name=row.name, kind=row.kind, username=row.username, domain=row.domain, secret=secret
    )
