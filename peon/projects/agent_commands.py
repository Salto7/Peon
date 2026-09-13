"""Detect / unwrap agent-emitted skill commands (not objective briefs)."""

from __future__ import annotations

import re

_RUN_SCRIPT_CMD = re.compile(
    r"""run_skill_script\s*\([^)]*command\s*=\s*(['"])(?P<cmd>.*?)(?<!\\)\1""",
    re.IGNORECASE | re.DOTALL,
)
_BRIEF_PREFIXES = (
    "execute project objective",
    "execute project",
    "operator re-run",
    "operator project",
    "operator follow-up",
    "operator instruction",
    "obj-",
)


def looks_like_agent_command(text: str) -> bool:
    """True for tool/CLI lines the graph should edit — not full job briefs."""
    raw = (text or "").strip()
    if not raw:
        return False
    low = raw.lower()
    if any(low.startswith(p) for p in _BRIEF_PREFIXES):
        return False
    if "\n" in raw and "acceptance criteria" in low:
        return False
    if "run_skill_script" in low or "sandbox_setup" in low:
        return True
    # Common CLI skill invocations (nmap, httpx, …).
    first = low.split(None, 1)[0] if low else ""
    if first in {
        "nmap",
        "httpx",
        "curl",
        "dig",
        "whois",
        "masscan",
        "nuclei",
        "ffuf",
    }:
        return True
    if first.endswith(".py") or "/" in first:
        return True
    return False


def unwrap_skill_command(text: str) -> str:
    """Prefer the inner ``command=`` from ``run_skill_script(...)``; else strip."""
    raw = (text or "").strip()
    if not raw:
        return ""
    m = _RUN_SCRIPT_CMD.search(raw)
    if m:
        return (m.group("cmd") or "").strip()
    return raw


def pick_agent_command(*candidates: str) -> str:
    """First candidate that looks like an emitted agent command (unwrapped)."""
    for raw in candidates:
        text = (raw or "").strip()
        if not text or not looks_like_agent_command(text):
            continue
        return unwrap_skill_command(text)
    return ""
