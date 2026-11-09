"""run_project_crew: a plain function now, not an ABC/DTO pair. No live LLM."""

from __future__ import annotations

from orchestrator.agent.config import AgentRunConfig
from orchestrator.agent.job import JobScope
from orchestrator.agent.backends.crewai import project as project_crewai
from tests.support.recording_bridge import RecordingBridge


class _FakeCrew:
    def __init__(self, raw: str) -> None:
        self._raw = raw

    def kickoff(self):
        return type("Result", (), {"raw": self._raw})()


def test_run_project_crew_returns_agent_run_result(monkeypatch):
    captured = {}

    def fake_build_engagement_crew(*, brief, role_ids, replan_note, config):
        captured["brief"] = brief
        captured["role_ids"] = role_ids
        captured["replan_note"] = replan_note
        captured["config"] = config
        return _FakeCrew("crew output")

    monkeypatch.setattr(
        project_crewai, "build_engagement_crew", fake_build_engagement_crew
    )

    bridge = RecordingBridge()
    scope = JobScope(job_id="job-1", bridge=bridge, brief="do the thing")
    result = project_crewai.run_project_crew(
        scope,
        AgentRunConfig(),
        role_ids=("specialist",),
        steer="focus on the api",
        flow_id="flow-1",
    )

    assert result.ok is True
    assert result.output == "crew output"
    assert result.iterations == 1
    assert captured["role_ids"] == ("specialist",)
    assert "do the thing" in captured["brief"]
    assert "focus on the api" in captured["brief"]
    assert scope.extras["crew_mode"] == "project"
    assert scope.extras["crew_flow_id"] == "flow-1"
    emitted = [call for call in bridge.calls if call[0] == "emit"]
    assert [args[0] for _, args, _ in emitted] == ["status", "status"]


def test_run_project_crew_disabled_runtime_short_circuits(monkeypatch):
    monkeypatch.setattr(
        project_crewai,
        "build_engagement_crew",
        lambda **_: (_ for _ in ()).throw(AssertionError("must not build a crew")),
    )
    scope = JobScope(job_id="job-2", bridge=RecordingBridge())
    result = project_crewai.run_project_crew(
        scope, AgentRunConfig(runtime_enabled=False)
    )
    assert result.ok is False
    assert "disabled" in result.error
