"""Normalize skill tags against the live catalog."""

from __future__ import annotations

from typing import Any, Iterable

from orchestrator.utils.service import SharedService
from orchestrator.utils.strings import as_str_list, unique, fold_keys


class TagNormalizer(SharedService):
    """Derive tag canonical spellings from known skill tags (no static alias table)."""

    @staticmethod
    def build_index(known_tags: Iterable[str]) -> dict[str, str]:
        index: dict[str, str] = {}
        for tag in known_tags:
            text = str(tag or "").strip()
            if not text:
                continue
            for key in fold_keys(text):
                index.setdefault(key, text)
        return index

    @staticmethod
    def collect_known(tag_lists: Iterable[Iterable[str]]) -> list[str]:
        known: list[str] = []
        seen: set[str] = set()
        for tags in tag_lists:
            for tag in tags or []:
                text = str(tag or "").strip()
                if text and text not in seen:
                    seen.add(text)
                    known.append(text)
        return known

    @staticmethod
    def normalize(
        raw: Any,
        *,
        known: Iterable[str] | None = None,
    ) -> list[str]:
        parts = as_str_list(raw)
        if not parts:
            return []
        known_list = [str(t).strip() for t in (known or []) if str(t).strip()]
        if not known_list:
            return unique(parts)

        index = TagNormalizer.build_index(known_list)
        out: list[str] = []
        for part in parts:
            canon = None
            for key in fold_keys(part):
                if key in index:
                    canon = index[key]
                    break
            chosen = canon or part
            if chosen not in out:
                out.append(chosen)
        return out
