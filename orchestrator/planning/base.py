"""Shared planner base: message normalize, context blocks, persist."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from orchestrator.utils.strings import plain_text
from orchestrator.workspace import plans_dir, provision_job_workspace


class BasePlanner(ABC):
    """Common plumbing for job and project planners."""

    plan_title: str = "Plan"

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """Planner system prompt for this mode."""

    def message_text(self, content: Any) -> str:
        """Normalize LLM message content to plain text."""
        return plain_text(content)

    def append_context(
        self,
        parts: list[str],
        *,
        prior_plan: str = "",
        memory_block: str = "",
    ) -> None:
        """Append shared optional context sections to the human message parts."""
        if memory_block.strip():
            parts.append(f"Procedural memory:\n{memory_block.strip()}")
        if prior_plan.strip():
            parts.append(f"Previous plan (revise if needed):\n{prior_plan.strip()}")

    def wrap_messages(self, human_parts: list[str]) -> list:
        return [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content="\n\n".join(human_parts)),
        ]

    def persist(self, job_id: str, plan_text: str, *, reason: str = "") -> str:
        """Save plan under job workspace plans/. Returns relative path."""
        if not job_id or not (plan_text or "").strip():
            return ""
        try:
            provision_job_workspace(job_id)
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
            body = self.message_text(plan_text).strip() or (plan_text or "").strip()
            md = (
                f"# {self.plan_title}\n\n"
                f"> Saved by planner ({reason or 'initial'}).\n\n"
                f"{body}\n"
            )
            dest = plans_dir(job_id) / f"{stamp}-plan.md"
            dest.write_text(md, encoding="utf-8")
            (plans_dir(job_id) / "latest.md").write_text(md, encoding="utf-8")
            return f"plans/{dest.name}"
        except Exception:
            return ""

    @abstractmethod
    def build_messages(self, description: str, **kwargs) -> list:
        """Build LangChain messages for the planner LLM."""

    @abstractmethod
    def format_result(self, raw_llm_text: str, **kwargs) -> str:
        """Turn raw LLM output into the final plan string."""
