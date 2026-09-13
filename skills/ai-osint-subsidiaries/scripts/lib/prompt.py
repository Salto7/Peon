"""One bulk AI prompt: map SEC subsidiaries → official domains."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.schema import entity_key, merge_rows, normalize_relationship, registrable_apex

PROMPT = """Parent / filer: {company}
SEC CIK: {cik}

Below is the subsidiary / related-entity list from SEC Exhibit 21.
For each name, supply the official public apex domain if you know it.
Never invent domains. Use "" when unknown.

ENTITIES:
{entities}

Return ONLY a JSON array (no markdown, no commentary):
[
  {{"entity": "<exact name from list>", "domain": "<apex or empty>", "confidence": "high|medium|low"}}
]

Rules:
- Match entity strings exactly. Include one object per entity (use "" when unknown).
- Include the parent if you know its domain.
- Prefer operating-company websites; skip pure holding shells when unknown.
- Official apex only (acme.com). Never invent.
"""

ANSWER_PATH = "workspace/raw/ai-osint-subsidiaries/ai-domains.json"

_BEGIN = "<<<PEON_MODEL_ANSWER>>>"
_END = "<<<END_PEON_MODEL_ANSWER>>>"


def format_model_answer_envelope(
    prompt: str,
    *,
    workspace: str = "workspace",
) -> str:
    """Ask the host runtime to fulfill this prompt (generic PEON envelope)."""
    ws = (workspace or "workspace").rstrip("/") or "workspace"
    answer = f"{ws}/raw/ai-osint-subsidiaries/ai-domains.json"
    header = {
        "version": 1,
        "format": "json",
        "write_answer": answer,
        "resume": {
            "skill": "ai-osint-subsidiaries",
            "asset": "scripts/run.py",
            "command": f"merge-ai --ai {{answer_path}} --workspace {ws}",
        },
    }
    return (
        f"{_BEGIN}\n"
        f"{json.dumps(header, indent=2)}\n"
        f"---\n"
        f"{(prompt or '').rstrip()}\n"
        f"{_END}\n"
    )


def rows_from(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict) and "findings" in data:
        data = data["findings"]
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, dict)]


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def render_prompt(*, company: str, cik: str, rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for row in rows:
        entity = str(row.get("entity") or "").strip()
        if not entity:
            continue
        rel = str(row.get("relationship") or "").strip() or "subsidiary"
        lines.append(f"- {entity} ({rel})")
    if not lines:
        raise ValueError("no entities to prompt on — run SEC pipeline first")
    return PROMPT.format(
        company=(company or "").strip() or "(unknown)",
        cik=(cik or "").strip() or "(unknown)",
        entities="\n".join(lines),
    )


def parse_ai_findings(data: Any) -> list[dict[str, str]]:
    rows = rows_from(data)
    out: list[dict[str, str]] = []
    for obj in rows:
        entity = str(obj.get("entity") or "").strip()
        if not entity:
            continue
        domain = registrable_apex(str(obj.get("domain") or ""))
        confidence = str(obj.get("confidence") or "medium").strip().lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "medium"
        out.append(
            {
                "entity": entity,
                "relationship": normalize_relationship(
                    str(obj.get("relationship") or "subsidiary")
                ),
                "domain": domain,
                "source": "ai",
                "evidence_url": "",
                "confidence": confidence,
            }
        )
    return out


def merge_ai_into_base(
    base: list[dict[str, str]],
    ai_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    enriched = [dict(r) for r in base]
    by_key = {entity_key(r.get("entity") or ""): i for i, r in enumerate(enriched)}
    for ai in ai_rows:
        key = entity_key(ai.get("entity") or "")
        if not key:
            continue
        if key in by_key:
            cur = enriched[by_key[key]]
            if not cur.get("domain") and ai.get("domain"):
                cur["domain"] = ai["domain"]
            enriched[by_key[key]] = cur
            continue
        if ai.get("domain"):
            enriched.append(ai)
    return merge_rows(enriched)
