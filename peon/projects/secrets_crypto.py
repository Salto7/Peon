"""At-rest encryption for project secrets (key store, credential env values).

Key is derived from ``settings.SECRET_KEY`` — no separate secret to provision,
but rotating ``SECRET_KEY`` makes previously stored ciphertext undecryptable.
Not a substitute for a real secrets manager; it keeps credentials out of
plaintext in the per-project SQLite file and off the browser.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class SecretDecryptError(Exception):
    """Ciphertext could not be decrypted (wrong/rotated key, or corrupt row)."""


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key_material = str(getattr(settings, "SECRET_KEY", "") or "").encode("utf-8")
    if not key_material:
        raise RuntimeError("SECRET_KEY must be set to use the project credential store")
    digest = hashlib.sha256(key_material).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    """Empty string in → empty string out (nothing to store)."""
    text = plaintext or ""
    if not text:
        return ""
    return _fernet().encrypt(text.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    text = (ciphertext or "").strip()
    if not text:
        return ""
    try:
        return _fernet().decrypt(text.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise SecretDecryptError("stored credential could not be decrypted") from exc
