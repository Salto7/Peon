"""LLM cost/token telemetry — one litellm callback, no per-call-site plumbing.

Every upstream call in Peon goes through ``litellm`` sooner or later: directly
(``orchestrator.llm.providers.litellm``, used by ``chat_text``/``chat_json``)
or inside CrewAI's own ``LLM`` wrapper (``Agent.kickoff``). Registering one
``litellm.success_callback``/``failure_callback`` pair therefore captures
every call site, including ones this module never imports.

Call sites tag *why* a call happened with :func:`purpose` (a context manager);
job runs are tagged automatically from ``JobEnv`` (job/project/role id), bound
once per job in ``peon.projects.job_run._bind_job_env``. A call with no
``purpose()`` and no job context is logged as ``"unlabeled"`` — a sign a call
site still needs tagging, not a bug.

This module does not import Django or Peon models at module scope: the
callback resolves them lazily so ``orchestrator`` stays usable without Django
(see ``orchestrator/config.py``). A write that fails (app registry not ready,
DB locked) is logged and dropped — telemetry must never break a real call.

litellm runs ``success_callback``/``failure_callback`` off the calling thread,
so a row typically lands a moment (not necessarily milliseconds) after the
``complete()``/``kickoff()`` call returns — never assume a row is already
written immediately after a call, e.g. in a script that calls then exits.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_purpose: ContextVar[str] = ContextVar("llm_usage_purpose", default="")
_registered = False


@contextmanager
def purpose(name: str) -> Iterator[None]:
    """Tag LLM calls made inside this block with ``name`` for cost reporting.

    Nests: an inner ``purpose()`` wins for calls made inside it, and the outer
    tag resumes for calls made after it exits.
    """
    token: Token = _purpose.set(str(name or "").strip() or "unlabeled")
    try:
        yield
    finally:
        _purpose.reset(token)


def current_purpose() -> str:
    return _purpose.get()


def _job_context() -> dict[str, str]:
    try:
        from orchestrator.utils.job_env import JobEnv
    except Exception:  # noqa: BLE001
        return {}
    return {
        "job_id": JobEnv.get("ORCHESTRATOR_JOB_ID"),
        "project_id": JobEnv.get("ORCHESTRATOR_PROJECT_ID"),
        "role_id": JobEnv.get("ORCHESTRATOR_ROLE_ID"),
    }


def _resolve_cost(kwargs: dict[str, Any], response: Any) -> float:
    """``response_cost`` litellm already computed, else ask it directly."""
    direct = kwargs.get("response_cost")
    if isinstance(direct, (int, float)):
        return float(direct)
    hidden = getattr(response, "hidden_params", None)
    hidden_cost = hidden.get("response_cost") if isinstance(hidden, dict) else None
    if isinstance(hidden_cost, (int, float)):
        return float(hidden_cost)
    try:
        import litellm

        return float(litellm.completion_cost(completion_response=response) or 0.0)
    except Exception:  # noqa: BLE001 — unknown/custom model pricing, not fatal
        return 0.0


def _usage_of(response: Any) -> tuple[int, int, int]:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    getter = usage.get if isinstance(usage, dict) else lambda k: getattr(usage, k, None)
    prompt = int(getter("prompt_tokens") or 0) if usage else 0
    completion = int(getter("completion_tokens") or 0) if usage else 0
    total = int(getter("total_tokens") or 0) if usage else prompt + completion
    return prompt, completion, total


def _write(row: dict[str, Any]) -> None:
    try:
        from peon.projects.models import LlmCallLog

        LlmCallLog.objects.using("default").create(**row)
    except Exception:  # noqa: BLE001 — telemetry must never break a real call
        logger.debug("llm usage log write failed", exc_info=True)


def _log_success(kwargs: dict[str, Any], response: Any, start_time, end_time) -> None:
    prompt_t, completion_t, total_t = _usage_of(response)
    duration_ms = int(max(0.0, (end_time - start_time).total_seconds()) * 1000)
    ctx = _job_context()
    _write(
        {
            "purpose": current_purpose() or "unlabeled",
            "model": str(kwargs.get("model") or ""),
            "project_id": ctx.get("project_id", "")[:36],
            "job_id": ctx.get("job_id", "")[:36],
            "role_id": ctx.get("role_id", "")[:64],
            "prompt_tokens": prompt_t,
            "completion_tokens": completion_t,
            "total_tokens": total_t,
            "cost_usd": _resolve_cost(kwargs, response),
            "duration_ms": duration_ms,
            "ok": True,
        }
    )


def _log_failure(kwargs: dict[str, Any], response: Any, start_time, end_time) -> None:
    del response
    duration_ms = int(max(0.0, (end_time - start_time).total_seconds()) * 1000)
    ctx = _job_context()
    error = str(kwargs.get("exception") or "")[:300]
    _write(
        {
            "purpose": current_purpose() or "unlabeled",
            "model": str(kwargs.get("model") or ""),
            "project_id": ctx.get("project_id", "")[:36],
            "job_id": ctx.get("job_id", "")[:36],
            "role_id": ctx.get("role_id", "")[:64],
            "duration_ms": duration_ms,
            "ok": False,
            "error": error,
        }
    )


def register() -> None:
    """Install the litellm callbacks once per process. Safe to call repeatedly."""
    global _registered
    if _registered:
        return
    try:
        import litellm
    except Exception:  # noqa: BLE001
        logger.warning("llm usage tracking skipped: litellm unavailable")
        return
    litellm.success_callback = [*(litellm.success_callback or []), _log_success]
    litellm.failure_callback = [*(litellm.failure_callback or []), _log_failure]
    _registered = True
    logger.info("llm usage tracking registered (litellm success/failure callbacks)")


def summary(*, since_hours: float = 24.0) -> list[dict[str, Any]]:
    """Cost/tokens/call-count grouped by purpose, most expensive first."""
    from django.db.models import Count, Sum
    from django.utils import timezone as dj_tz

    from peon.projects.models import LlmCallLog

    cutoff = dj_tz.now() - dj_tz.timedelta(hours=max(0.0, since_hours))
    rows = (
        LlmCallLog.objects.using("default")
        .filter(created_at__gte=cutoff)
        .values("purpose")
        .annotate(
            calls=Count("id"),
            failed=Count("id", filter=_q_not_ok()),
            cost_usd=Sum("cost_usd"),
            prompt_tokens=Sum("prompt_tokens"),
            completion_tokens=Sum("completion_tokens"),
        )
        .order_by("-cost_usd")
    )
    return list(rows)


def _q_not_ok():
    from django.db.models import Q

    return Q(ok=False)
