"""Build findings/report.md for offline / non-worker analyzer runs.

Peon worker synthesizes reports via ``peon.projects.analysis`` and does not
rely on this module. Keep this skill-side path free of peon/orchestrator imports.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def build_report(ws: Path) -> Path:
    findings_dir = ws / "findings"
    findings_dir.mkdir(parents=True, exist_ok=True)
    report = findings_dir / "report.md"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    plan = ""
    try:
        plan = (ws / "plans" / "latest.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass

    phase_bits: list[str] = []
    if findings_dir.is_dir():
        for path in sorted(findings_dir.glob("*.md")):
            if path.name.lower() == "report.md":
                continue
            try:
                body = path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if body:
                phase_bits.append(f"### `{path.name}`\n\n{body}")

    body = "\n".join(
        [
            "# Project report",
            "",
            f"Generated: {stamp}",
            "Synthesizer: analyzer skill (offline)",
            "",
            "## Executive summary",
            "",
            "Offline skill report. Prefer the peon worker analyzer for structured "
            "Finding rows and LLM synthesis.",
            "",
            "## Scope / plan coverage",
            "",
            plan.strip() or "_No plan._",
            "",
            "## Findings",
            "",
            "\n\n".join(phase_bits) or "_No phase findings._",
            "",
            "## Gaps / next steps",
            "",
            "- Re-run under the peon worker for full analysis synthesis.",
            "",
        ]
    )
    report.write_text(body, encoding="utf-8")
    return report
