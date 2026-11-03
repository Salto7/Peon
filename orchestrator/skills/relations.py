"""Apply ``CatalogSkillRelation`` rules to a skill set.

Soft kinds (sequence, runs_under, overlaps, conditioned rows) reach the model
via the progressive skill index. Hard kinds with empty ``conditions`` reshape
the bound id list here so Peon actually respects them at bind time.
"""

from __future__ import annotations

from typing import Any


def _rows() -> list[dict[str, str]]:
    try:
        from peon.projects.platform_catalog import PlatformCatalog

        return list(PlatformCatalog.shared().list_skill_relations())
    except Exception:
        return []


def _always(row: dict[str, Any]) -> bool:
    return not str(row.get("conditions") or "").strip()


def apply_skill_relations(skill_ids: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Filter/order a candidate skill list using hard catalog relations.

    - ``supersedes`` / ``preferred_over``: drop the related skill when the
      preferred one is present.
    - ``does_not_run_with``: drop the later id when both would bind.
    - ``depends_on``: insert the dependency ahead of the dependent if missing.
    Conditioned rows are skipped here (surfaced to the model instead).
    """
    ordered = [str(s).strip() for s in skill_ids if str(s).strip()]
    if not ordered:
        return ()
    present = set(ordered)
    rows = [r for r in _rows() if _always(r)]

    drop: set[str] = set()
    for r in rows:
        a, b, kind = r["skill_id"], r["related_skill_id"], r["kind"]
        if kind in {"supersedes", "preferred_over"} and a in present and b in present:
            drop.add(b)
        elif kind == "does_not_run_with" and a in present and b in present:
            drop.add(b if ordered.index(a) <= ordered.index(b) else a)

    if drop:
        ordered = [s for s in ordered if s not in drop]
        present = set(ordered)

    for r in rows:
        if r["kind"] != "depends_on":
            continue
        dep, need = r["skill_id"], r["related_skill_id"]
        if dep not in present:
            continue
        if need not in present:
            idx = ordered.index(dep)
            ordered.insert(idx, need)
            present.add(need)
        elif ordered.index(need) > ordered.index(dep):
            ordered.remove(need)
            ordered.insert(ordered.index(dep), need)

    return tuple(ordered)
