#!/usr/bin/env python3
"""Write or confirm plans/latest.md from job context (project planner bookend)."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from context import SkillContext
from skill_entry import main, workspace_path


def _fmt_targets(assets: list[dict]) -> str:
    if not assets:
        return "(none stated)"
    lines = []
    for t in assets:
        typ = (t.get("type") or "other").strip()
        val = (t.get("value") or "").strip()
        if not val:
            continue
        lines.append(f"- `{typ}:{val}`" if typ and typ != "other" else f"- `{val}`")
    return "\n".join(lines) or "(none stated)"


def _synthesize_plan(ctx: SkillContext) -> str:
    brief = (os.environ.get("ORCHESTRATOR_JOB_BRIEF") or "").strip()
    project = (os.environ.get("ORCHESTRATOR_PROJECT_ID") or "").strip()
    scope = ctx.in_scope_assets()
    seed = ctx.seed_assets()
    excl = ctx.exclusion_assets()
    goal = brief or "Confirm engagement scope and produce an actionable plan."
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return "\n".join(
        [
            "# Project Blueprint",
            "",
            f"_Generated {stamp}"
            + (f" · project `{project}`" if project else "")
            + "_",
            "",
            "## Engagement goal",
            goal,
            "",
            "## Scope and constraints",
            "### In scope",
            _fmt_targets(scope),
            "",
            "### Seed / intent",
            _fmt_targets(seed),
            "",
            "### Exclusions",
            _fmt_targets(excl),
            "",
            "- Discoveries remain candidates/findings until explicitly promoted.",
            "- No engagement activities are executed during the blueprint phase.",
            "",
            "## Phases",
            "1. **Blueprint / recon gate** — confirm this plan before execution.",
            "2. **Engagement work** — run authorized skills against in-scope values only.",
            "3. **Analysis / closeout** — synthesize evidence into `findings/report.md`.",
            "",
            "## Success criteria",
            "- `plans/latest.md` exists and reflects the engagement goal.",
            "- Actions remain limited to authorized in-scope values unless scope is promoted.",
            "- Evidence stays under `workspace/`; curated notes under `findings/`.",
            "",
        ]
    )


def _write_plan(plans: Path, body: str) -> Path:
    plans.mkdir(parents=True, exist_ok=True)
    latest = plans / "latest.md"
    text = (body or "").strip() + "\n"
    latest.write_text(text, encoding="utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    archived = plans / f"{stamp}-plan.md"
    archived.write_text(text, encoding="utf-8")
    return latest


def _run() -> int:
    ctx = SkillContext()
    ws = workspace_path()
    plans = ws / "plans"
    latest = plans / "latest.md"
    command = ctx.explicit_command().strip()

    # Agent may pass markdown via run_skill_script(..., command="<markdown>").
    if command and (command.lstrip().startswith("#") or "\n" in command):
        path = _write_plan(plans, command)
        print(f"blueprint wrote: {path}")
        print("validation=pass")
        return 0

    if latest.is_file() and latest.stat().st_size > 0:
        body = latest.read_text(encoding="utf-8", errors="replace")
        # Replace empty bookend stubs from older runs.
        if "Blueprint bookend — plan missing" in body or body.strip() == "# Project plan":
            path = _write_plan(plans, _synthesize_plan(ctx))
            print(f"blueprint replaced stub: {path}")
        else:
            print(f"blueprint ok: {latest}")
        print("validation=pass")
        return 0

    path = _write_plan(plans, _synthesize_plan(ctx))
    print(f"blueprint wrote: {path}")
    print("validation=pass")
    return 0


if __name__ == "__main__":
    main(_run)
