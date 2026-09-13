"""Shared LLM availability gate (plan / chat / Learn)."""

from __future__ import annotations


def llm_configured() -> bool:
    from orchestrator.utils.llm import llm_config

    return bool((llm_config().api_key or "").strip())
