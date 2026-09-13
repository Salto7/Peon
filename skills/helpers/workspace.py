"""Project workspace root + phase artifact persistence."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from context import SkillContext


class WorkspaceStore:
    """Resolve the bound project workspace and write analyzer-facing artifacts."""

    def __init__(self, context: SkillContext | None = None) -> None:
        self._ctx = context or SkillContext()

    def path(self) -> Path:
        """Trusted workspace under PROJECT_WORKSPACES_DIR / ORCHESTRATOR_WORKSPACE."""
        fallback = (Path.cwd() / "workspace").resolve()
        raw = (
            os.environ.get("ORCHESTRATOR_WORKSPACE")
            or os.environ.get("JOB_WORKSPACE")
            or ""
        ).strip()
        root_raw = (os.environ.get("PROJECT_WORKSPACES_DIR") or "").strip()
        roots: list[Path] = [Path.cwd().resolve()]
        if root_raw:
            roots.insert(0, Path(root_raw).resolve())

        def _under_roots(candidate: Path) -> Path | None:
            for root in roots:
                try:
                    candidate.relative_to(root)
                    return candidate
                except ValueError:
                    continue
            return None

        if raw:
            if ".." in Path(raw).parts:
                return fallback
            bound = _under_roots(Path(raw).resolve())
            return bound if bound is not None else fallback
        job = self._ctx.job_id()
        if job and root_raw:
            if any(ch in job for ch in ("/", "\\")) or ".." in job:
                return fallback
            bound = _under_roots((Path(root_raw) / job).resolve())
            return bound if bound is not None else fallback
        return fallback

    def persist_skill_run(
        self,
        *,
        skill: str,
        binary: str,
        argv: list[str],
        stdout: str,
        stderr: str,
        code: int,
    ) -> None:
        """Write raw + curated markdown for the analyzer (never findings/report.md)."""
        name = (skill or "").strip() or "skill"
        safe = (
            "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in name).strip("-")
            or "skill"
        )
        ws = self.path()
        raw_dir = ws / "workspace" / "raw" / safe
        findings = ws / "findings"
        try:
            raw_dir.mkdir(parents=True, exist_ok=True)
            findings.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        out_body = (stdout or "").rstrip()
        err_body = (stderr or "").rstrip()
        cmd_lines = [a for a in argv if a] or [binary]
        cmd_block = "\n".join(f"- `{c}`" for c in cmd_lines)
        try:
            (raw_dir / f"{binary}.out.txt").write_text(
                f"# {safe} / {binary}\n# exit={code}\n\n## commands\n{cmd_block}\n\n"
                f"## stdout\n{out_body or '(empty)'}\n\n"
                f"## stderr\n{err_body or '(empty)'}\n",
                encoding="utf-8",
            )
        except OSError:
            return
        lines = [
            f"# {safe}",
            "",
            f"Generated: {stamp}",
            f"Binary: `{binary}`",
            f"Exit: `{code}`",
            "",
            "## Commands",
            "",
            cmd_block,
            "",
            "## Output",
            "",
        ]
        if out_body:
            lines.extend(["```", out_body, "```", ""])
        else:
            lines.extend(["_No stdout._", ""])
        if err_body and code != 0:
            lines.extend(["## Stderr", "", "```", err_body, "```", ""])
        try:
            (findings / f"{safe}.md").write_text("\n".join(lines), encoding="utf-8")
        except OSError:
            return
