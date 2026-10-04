"""Project-level CrewAI runtime (hierarchical manager crew)."""

from __future__ import annotations

import uuid
from typing import Any

from orchestrator.agent.config import AgentRunConfig
from orchestrator.agent.job import JobScope, bind_job
from orchestrator.crew.checkpoint import (
    build_memory,
    checkpoint_config,
    output_log_path,
)
from orchestrator.crew.flows.engagement import build_engagement_crew
from orchestrator.crew.roles.factory import restore_agent_runtime_policy
from orchestrator.crew.roles.hierarchy import manager_role
from orchestrator.crew.runtime_base import (
    CrewRunRequest,
    CrewRunResult,
    CrewRuntimeBase,
)
from orchestrator.crew.runtime_support import drain_agent_inbox


class ProjectCrewRuntime(CrewRuntimeBase):
    """Run manager → specialists → analyzer for one project."""

    def start(self, request: CrewRunRequest) -> CrewRunResult:
        meta = dict(request.metadata or {})
        scope: JobScope | None = meta.get("scope")
        config: AgentRunConfig | None = meta.get("config")
        if scope is None:
            return CrewRunResult(
                ok=False,
                error="ProjectCrewRuntime requires metadata['scope'] (JobScope)",
                status="stopped",
                flow_id=request.flow_id,
            )
        cfg = config or AgentRunConfig()
        if not cfg.runtime_enabled:
            return CrewRunResult(
                ok=False,
                error="agent runtime disabled (AGENT_RUNTIME_ENABLED=false)",
                status="stopped",
                flow_id=request.flow_id,
            )

        flow_id = (request.flow_id or "").strip() or str(uuid.uuid4())
        brief = (request.brief or scope.brief or "").strip()
        operator_context = (request.steer or "").strip()

        scope.extras = dict(scope.extras or {})
        mgr = manager_role()
        if mgr:
            scope.extras["role_id"] = mgr.id
        scope.extras["crew_flow_id"] = flow_id
        scope.extras["crew_mode"] = "project"

        inbox = drain_agent_inbox(scope)
        if inbox:
            operator_context = (
                f"{operator_context}\n\n{inbox}".strip()
                if operator_context
                else inbox
            )
        if request.resume:
            scope.bridge.emit(
                "status",
                "resuming project crew from native CrewAI checkpoint",
                metadata={
                    "event": "agent_resume",
                    "thread_id": str(scope.job_id),
                    "flow_id": flow_id,
                },
            )

        try:
            memory = build_memory(scope) if cfg.memory_enabled else None
            checkpoint = (
                checkpoint_config(scope, resume=False)
                if cfg.checkpoint_enabled
                else None
            )
            crew = build_engagement_crew(
                role_ids=request.role_ids,
                max_iterations=cfg.max_iterations,
                max_replans=cfg.max_failure_replans,
                max_execution_time=cfg.max_execution_seconds or None,
                memory=memory,
                checkpoint=checkpoint,
                output_log_file=output_log_path(scope),
            )
        except Exception as exc:
            scope.bridge.emit("error", f"crew build failed: {exc}")
            return CrewRunResult(
                ok=False,
                error=str(exc),
                flow_id=flow_id,
                status="stopped",
            )

        action = "replan" if request.replan else ("resume" if request.resume else "start")
        scope.bridge.emit(
            "status",
            f"project crew {action} flow={flow_id[:8]}",
            metadata={
                "event": "crew_project_start",
                "flow_id": flow_id,
                "action": action,
            },
        )

        with bind_job(scope, cfg):
            try:
                restore = (
                    checkpoint_config(scope, resume=True)
                    if request.resume and cfg.checkpoint_enabled
                    else None
                )
                if restore is not None and restore.restore_from is not None:
                    from crewai import Crew

                    crew = Crew.from_checkpoint(restore)
                    crew.memory = memory
                    crew.create_crew_memory()
                    crew.checkpoint = checkpoint
                    crew.output_log_file = output_log_path(scope)
                    restored_agents = [*crew.agents]
                    if crew.manager_agent is not None:
                        restored_agents.append(crew.manager_agent)
                    for restored_agent in restored_agents:
                        restore_agent_runtime_policy(
                            restored_agent,
                            max_iterations=cfg.max_iterations,
                            max_replans=cfg.max_failure_replans,
                            max_execution_time=cfg.max_execution_seconds or None,
                            memory=None,
                            checkpoint=None,
                        )
                result = crew.kickoff(
                    inputs={
                        "brief": brief[:6000],
                        "operator_context": operator_context[:4000] or "(none)",
                    },
                )
            except Exception as exc:
                scope.bridge.emit("error", f"project crew failed: {exc}")
                return CrewRunResult(
                    ok=False,
                    error=str(exc),
                    flow_id=flow_id,
                    status="stopped",
                )

        output = _crew_output(result)
        scope.bridge.emit(
            "status",
            f"project crew done flow={flow_id[:8]}",
            metadata={"event": "crew_project_done", "flow_id": flow_id},
        )
        usage = getattr(crew, "usage_metrics", None)
        return CrewRunResult(
            ok=True,
            output=output,
            flow_id=flow_id,
            status="done",
            metadata={
                "action": action,
                "usage": usage.model_dump() if hasattr(usage, "model_dump") else {},
            },
        )


def _crew_output(result: Any) -> str:
    if result is None:
        return ""
    raw = getattr(result, "raw", None)
    if raw is not None:
        return str(raw)
    return str(result)


def run_project_crew_from_scope(
    scope: JobScope,
    config: AgentRunConfig | None = None,
    *,
    role_ids: tuple[str, ...] = (),
    resume: bool = False,
    replan: bool = False,
    steer: str = "",
    flow_id: str = "",
) -> CrewRunResult:
    """Helper used by the job runtime when the manager role owns the project."""
    runtime = ProjectCrewRuntime()
    return runtime.start(
        CrewRunRequest(
            project_id=scope.project_id,
            brief=scope.brief,
            role_ids=role_ids,
            resume=resume,
            replan=replan,
            steer=steer,
            flow_id=flow_id or str(scope.extras.get("crew_flow_id") or ""),
            metadata={"scope": scope, "config": config or AgentRunConfig()},
        )
    )
