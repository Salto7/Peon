"""Match catalog skill names (and aliases) in free-text prompts."""

from __future__ import annotations

import re
from typing import Iterable

from orchestrator.utils.service import SharedService
from orchestrator.utils.strings import name_forms


class SkillNameMatcher(SharedService):
    """Find registered skill ids in operator text (order-preserving, non-overlapping)."""

    @staticmethod
    def find(description: str, valid: Iterable[str]) -> list[str]:
        hay = (description or "").lower()
        if not hay:
            return []

        candidates: list[tuple[int, int, str]] = []
        names = {str(n).strip() for n in valid if str(n).strip()}
        for name in names:
            if len(name) < 3:
                continue
            marked = "-" in name or "_" in name
            for alias in name_forms(name):
                token = rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])"
                pattern = token if marked else rf"(?:skill\s+{token}|{token}\s+skill)"
                candidates.extend(
                    (match.start(), match.end(), name) for match in re.finditer(pattern, hay)
                )

        found: list[str] = []
        seen: set[str] = set()
        cursor = -1
        for start, end, name in sorted(
            candidates,
            key=lambda match: (match[0], -(match[1] - match[0]), match[2]),
        ):
            if start < cursor:
                continue
            cursor = end
            if name not in seen:
                seen.add(name)
                found.append(name)
        return found
