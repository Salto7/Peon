"""Durable per-Job LangGraph agent runtime."""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from orchestrator.agent.config import AgentRunConfig
from orchestrator.agent.graph import build_agent_graph, extract_output_text
from orchestrator.agent.job import JobScope, bind_job
from orchestrator.agent.runtime_base import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeBase,
    CheckpointFactory,
)
from orchestrator.config import get_config


class LangGraphAgentRuntime(AgentRuntimeBase):
    """Durable per-Job LangGraph loop; ``thread_id`` = ``job_id``."""

    def start(self, request: AgentRunRequest) -> AgentRunResult:
        scope, config = request.scope, request.config
        if not config.runtime_enabled:
            return AgentRunResult(
                ok=False,
                error="agent runtime disabled (AGENT_RUNTIME_ENABLED=false)",
            )
        if not (scope.skill_names or scope.brief.strip()):
            return AgentRunResult(ok=False, error="no skill_names or brief on job scope")

        try:
            ok, output, iterations = self._invoke(scope, config, request)
        except Exception as exc:
            scope.bridge.emit("error", f"agent failed: {exc}")
            return AgentRunResult(ok=False, error=str(exc), output="")

        scope.bridge.emit(
            "status",
            f"agent finished ok={ok} iterations={iterations}",
            metadata={"event": "agent_done", "iterations": iterations},
        )
        return AgentRunResult(ok=ok, output=output, iterations=iterations)

    def _invoke(
        self,
        scope: JobScope,
        config: AgentRunConfig,
        request: AgentRunRequest,
    ) -> tuple[bool, str, int]:
        rt = get_config()
        checkpointer = CheckpointFactory.shared().get(
            backend=getattr(rt, "agent_checkpoint_backend", "auto") or "auto",
            sqlite_path=getattr(rt, "agent_checkpoint_path", None) or None,
        )
        compiled = build_agent_graph(scope, config, checkpointer=checkpointer)
        thread = {"configurable": {"thread_id": str(scope.job_id)}}
        recursion = {"recursion_limit": max(10, config.max_iterations * 2 + 5)}

        steer = (request.steer or "").strip()
        if request.resume:
            text = steer or (
                "Continue from your last checkpoint under Rules of Engagement. "
                "Do not repeat completed work; proceed with the next useful step."
            )
            payload = {"messages": [HumanMessage(content=text[:8000])]}
            scope.bridge.emit(
                "status",
                "resuming agent from checkpoint",
                metadata={"event": "agent_resume", "thread_id": str(scope.job_id)},
            )
        else:
            human = (
                scope.brief or f"Execute skills: {', '.join(scope.skill_names)}"
            ).strip()
            if steer:
                human = f"{human}\n\nOPERATOR INSTRUCTION:\n{steer}".strip()
            payload = {
                "messages": [HumanMessage(content=human[:8000])],
                "iterations": 0,
                "failure_replans": 0,
            }

        with bind_job(scope, config):
            final = compiled.invoke(payload, {**thread, **recursion})

        iterations = int((final or {}).get("iterations") or 0)
        text = extract_output_text(final or {})
        return iterations > 0 or bool(text and text != "(no agent text)"), text, iterations
