"""CatalogCardsBase — shared helpers for role/tool UI card projections."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class CatalogCardsBase(ABC):
    """Shared helpers for projecting catalog assets into UI card dicts."""

    DESC_LIMIT = 280
    PICKER_DESC_LIMIT = 200
    TAG_LIMIT = 12
    PICKER_TAG_LIMIT = 8
    ALIAS_LIMIT = 8

    # Section toolbar / reload — subclasses set these and override ``reload``.
    SECTION = ""
    LABEL = ""
    RELOAD_URL_NAME = ""
    RELOAD_TITLE = ""

    @staticmethod
    def text(value: object, *, limit: int | None = None) -> str:
        text = str(value or "").strip()
        if limit is not None:
            return text[:limit]
        return text

    @classmethod
    def labels(cls, values: list | None, *, limit: int | None = None) -> list[str]:
        cap = cls.TAG_LIMIT if limit is None else limit
        out: list[str] = []
        for raw in values or []:
            item = str(raw).strip()
            if not item or item in out:
                continue
            out.append(item)
            if len(out) >= cap:
                break
        return out

    @classmethod
    def base(
        cls,
        *,
        name: str,
        description: str = "",
        tags: list | None = None,
        desc_limit: int | None = None,
        tag_limit: int | None = None,
    ) -> dict[str, Any]:
        """Fields shared by every catalog card."""
        return {
            "name": cls.text(name),
            "description": cls.text(
                description,
                limit=cls.DESC_LIMIT if desc_limit is None else desc_limit,
            ),
            "tags": cls.labels(tags, limit=cls.TAG_LIMIT if tag_limit is None else tag_limit),
        }

    @classmethod
    @abstractmethod
    def catalog(cls, **kwargs: Any) -> list[dict]:
        """Return card dicts for the full catalog listing."""

    @classmethod
    @abstractmethod
    def reload(cls) -> dict[str, list]:
        """Force-rescan backing store; return added/removed/modified diff."""

    @classmethod
    def reload_flash(cls, diff: dict) -> str:
        added = len(diff.get("added") or [])
        removed = len(diff.get("removed") or [])
        modified = len(diff.get("modified") or [])
        return (
            f"{cls.LABEL} updated — added {added}, removed {removed}, "
            f"modified {modified}."
        )

    @classmethod
    def reload_button(cls) -> dict[str, str]:
        """Toolbar metadata for the section Update button."""
        return {
            "url_name": cls.RELOAD_URL_NAME,
            "title": cls.RELOAD_TITLE,
            "label": f"Update {cls.SECTION}",
            "section": cls.SECTION,
        }
