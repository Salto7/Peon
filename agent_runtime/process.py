"""Subprocess helper for runtime backends only.

Role assets, catalog CLIs, and operator commands go through ``RuntimeSession.exec``.
"""

from __future__ import annotations

import os
import subprocess

from agent_runtime.api import ExecResult


def run_process(
    cmd: list[str] | str,
    *,
    timeout: float | None = 300,
    shell: bool = False,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> ExecResult:
    merged = {**os.environ, "DEBIAN_FRONTEND": "noninteractive", **(env or {})}
    try:
        proc = subprocess.run(
            cmd,
            shell=shell,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=merged,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        err = (
            exc.stderr
            if isinstance(exc.stderr, str)
            else f"timed out after {timeout}s"
        )
        return ExecResult(124, out, err)
    return ExecResult(proc.returncode or 0, proc.stdout or "", proc.stderr or "")
