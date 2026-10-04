"""Durable, framework-neutral checkpoints for CrewAI job runs."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.agent.job import JobScope

_MAX_EVENTS = 50


def _checkpoint_path(scope: JobScope) -> Path | None:
    root = str(scope.workspace or "").strip()
    if not root:
        return None
    job_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(scope.job_id or "job"))
    return Path(root) / ".peon" / "crew-checkpoints" / f"{job_id}.json"


def load_checkpoint(scope: JobScope) -> dict[str, Any]:
    """Load this job's last valid checkpoint, if one exists."""
    path = _checkpoint_path(scope)
    if path is None or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def save_checkpoint(scope: JobScope, **updates: Any) -> None:
    """Atomically merge fields into this job's checkpoint."""
    path = _checkpoint_path(scope)
    if path is None:
        return
    current = load_checkpoint(scope)
    current.update(updates)
    current.update(
        {
            "job_id": str(scope.job_id),
            "project_id": str(scope.project_id or ""),
            "role_id": str((scope.extras or {}).get("role_id") or ""),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f"{path.suffix}.tmp")
        tmp.write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        # Checkpointing must not turn an otherwise valid engagement into a failure.
        return


def record_checkpoint_event(scope: JobScope, kind: str, content: str) -> None:
    """Append a bounded event useful for reconstructing context on resume."""
    current = load_checkpoint(scope)
    events = current.get("events")
    if not isinstance(events, list):
        events = []
    events.append(
        {
            "kind": str(kind or "event")[:64],
            "content": str(content or "")[:4000],
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )
    save_checkpoint(scope, events=events[-_MAX_EVENTS:])


def resume_context(scope: JobScope) -> str:
    """Render bounded prior state for a resumed CrewAI run."""
    state = load_checkpoint(scope)
    if not state:
        return ""
    chunks: list[str] = []
    output = str(state.get("output") or "").strip()
    if output:
        chunks.append("Previous run output:\n" + output[-6000:])
    events = state.get("events")
    if isinstance(events, list):
        lines: list[str] = []
        for event in events[-12:]:
            if not isinstance(event, dict):
                continue
            kind = str(event.get("kind") or "event")
            content = str(event.get("content") or "").strip()
            if content:
                lines.append(f"- [{kind}] {content[:800]}")
        if lines:
            chunks.append("Recent checkpoint events:\n" + "\n".join(lines))
    return "\n\n".join(chunks)
