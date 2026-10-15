"""Build findings/report.md for offline / non-worker analyzer runs.

Required layout (see ../references/REPORT.md):

1. Executive summary
2. Findings and observations
   - Steps taken and commands executed
   - Findings and their technical details
3. Recommendations

Keep this skill-side path free of peon/orchestrator imports.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

_SEV_ORDER = ["critical", "high", "medium", "low", "info"]
_OUTPUT_CAP = 1500


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _section(text: str, name: str) -> str:
    m = re.search(rf"^## {re.escape(name)}\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


def _clip(text: str, cap: int = _OUTPUT_CAP) -> str:
    text = text.strip()
    return text if len(text) <= cap else text[:cap].rstrip() + "\n… (truncated)"


def _runs(findings_dir: Path) -> list[dict]:
    """Parse per-skill ``findings/<skill>.md`` run notes into structured rows."""
    runs: list[dict] = []
    if not findings_dir.is_dir():
        return runs
    for path in sorted(findings_dir.glob("*.md")):
        if path.name.lower() == "report.md":
            continue
        text = _read(path).strip()
        if not text:
            continue
        cmds = re.findall(r"^- `(.+)`\s*$", _section(text, "Commands"), re.M)
        exit_m = re.search(r"^Exit:\s*`?(-?\d+)`?", text, re.M)
        bin_m = re.search(r"^Binary:\s*`?([^`\n]+)`?", text, re.M)
        out = _section(text, "Output")
        out = re.sub(r"^```[a-zA-Z]*\n?|\n?```\s*$", "", out.strip())
        runs.append(
            {
                "skill": path.stem,
                "binary": bin_m.group(1).strip() if bin_m else "",
                "exit": int(exit_m.group(1)) if exit_m else None,
                "commands": cmds,
                "output": out,
                "body": text,
            }
        )
    return runs


def _queue(ws: Path) -> list[dict]:
    rows: list[dict] = []
    for line in _read(ws / "workspace" / "findings_queue.jsonl").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _sev(row: dict) -> str:
    s = str(row.get("severity") or "info").lower()
    return s if s in _SEV_ORDER else "info"


def _finding_line(row: dict) -> str:
    title = str(row.get("title") or row.get("name") or row.get("kind") or "finding")
    sev = _sev(row).upper()
    bits = [f"**[{sev}] {title}**"]
    asset = row.get("asset") or row.get("target") or row.get("host")
    if asset:
        bits.append(f"Asset: `{asset}`")
    detail = row.get("detail") or row.get("description") or row.get("evidence")
    if detail:
        bits.append(str(detail))
    remediation = row.get("remediation") or row.get("recommendation")
    if remediation:
        bits.append(f"Remediation: {remediation}")
    return "\n  ".join(str(b) for b in bits)


def _executive_summary(runs: list[dict], rows: list[dict]) -> str:
    n_runs = len(runs)
    n_fail = sum(1 for r in runs if r.get("exit") not in (None, 0))
    by_sev: dict[str, int] = {}
    for row in rows:
        by_sev[_sev(row)] = by_sev.get(_sev(row), 0) + 1
    sev_bits = ", ".join(
        f"{by_sev[s]} {s}" for s in _SEV_ORDER if by_sev.get(s)
    ) or "no structured findings recorded"
    lines = [
        f"- {n_runs} tool run(s) executed across the engagement"
        + (f" ({n_fail} with a non-zero exit)" if n_fail else ""),
        f"- Findings board: {sev_bits}.",
    ]
    return "\n".join(lines)


def build_report(ws: Path) -> Path:
    findings_dir = ws / "findings"
    findings_dir.mkdir(parents=True, exist_ok=True)
    report = findings_dir / "report.md"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    runs = _runs(findings_dir)
    rows = _queue(ws)
    rows.sort(key=lambda r: _SEV_ORDER.index(_sev(r)))

    steps: list[str] = []
    for run in runs:
        head = f"### `{run['skill']}`"
        if run["binary"]:
            head += f" — `{run['binary']}`"
        if run["exit"] is not None:
            head += f" (exit {run['exit']})"
        cmd_block = (
            "\n".join(f"- `{c}`" for c in run["commands"])
            if run["commands"]
            else "_No commands recorded._"
        )
        out_block = _clip(run["output"]) or "_No output captured._"
        steps.append(
            f"{head}\n\nCommands:\n{cmd_block}\n\nOutput excerpt:\n```\n{out_block}\n```"
        )

    findings_detail = (
        "\n\n".join(_finding_line(r) for r in rows)
        if rows
        else "_No structured findings were recorded for this engagement._"
    )

    recommendations: list[str] = []
    for row in rows:
        rec = row.get("remediation") or row.get("recommendation")
        if rec:
            title = row.get("title") or row.get("name") or row.get("kind") or "finding"
            recommendations.append(f"- **{title}**: {rec}")
    for run in runs:
        if run.get("exit") not in (None, 0):
            recommendations.append(
                f"- Re-run `{run['skill']}` (`{run['binary'] or 'tool'}`) — "
                f"it exited non-zero (`{run['exit']}`); investigate before relying on its output."
            )
    if not recommendations:
        recommendations.append(
            "- No remediation items were attached to findings; re-run under the peon "
            "worker analyzer for structured recommendations."
        )

    body = "\n".join(
        [
            "# Security report",
            "",
            f"Generated: {stamp}",
            "Synthesizer: analyzer skill (offline)",
            "",
            "## Executive summary",
            "",
            _executive_summary(runs, rows),
            "",
            "## Findings and observations",
            "",
            "### Steps taken and commands executed",
            "",
            "\n\n".join(steps) or "_No tool runs recorded._",
            "",
            "### Findings and their technical details",
            "",
            findings_detail,
            "",
            "## Recommendations",
            "",
            "\n".join(recommendations),
            "",
        ]
    )
    report.write_text(body, encoding="utf-8")
    return report
