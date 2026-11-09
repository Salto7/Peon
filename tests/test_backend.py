"""Registry: one production backend, and a fake proves the second-id slot."""

from __future__ import annotations

from orchestrator.agent.backends.crewai.backend import CrewAIBackend
from orchestrator.agent.config import AgentRunConfig
from orchestrator.agent.job import JobScope
from orchestrator.agent.registry import _BACKENDS
from orchestrator.agent.runtime_base import (
    AgentRunResult,
    get_agent_runtime,
    run_agent,
    set_agent_runtime,
)
from orchestrator.config import configure, get_config
from tests.support.recording_bridge import RecordingBridge


def test_production_registry_is_only_crewai():
    assert list(_BACKENDS) == ["crewai"]
    assert _BACKENDS["crewai"] == "orchestrator.agent.backends.crewai:CrewAIBackend"


def test_fake_backend_runs_manager_as_a_single_role_job(monkeypatch):
    monkeypatch.setitem(
        _BACKENDS, "fake", "tests.support.fake_backend:FakeBackend"
    )
    configure(get_config().merge(agent_module="fake"))
    set_agent_runtime(None)
    scope = JobScope(
        job_id="job-9",
        bridge=object(),
        extras={"crew_mode": "project", "role_id": "project-manager"},
    )
    try:
        backend = get_agent_runtime()
        assert type(backend).__name__ == "FakeBackend"
        assert backend.supports_project_crew is False
        assert backend.warm_calls == 0
        backend.warm()
        assert backend.warm_calls == 1

        skipped = run_agent(scope, AgentRunConfig())
        assert skipped.output == "job:job-9"
        assert skipped.iterations == 2

        backend.supports_project_crew = True
        ran = run_agent(scope, AgentRunConfig())
        assert ran.output == "project:job-9"
        assert ran.iterations == 3
        assert get_agent_runtime() is backend
    finally:
        configure(get_config().merge(agent_module="crewai"))
        set_agent_runtime(None)


def test_crewai_project_mode_still_runs_the_project_crew(monkeypatch):
    captured: dict = {}

    def fake_run_project_crew(scope, config, **kwargs):
        captured["role_ids"] = kwargs["role_ids"]
        captured["flow_id"] = kwargs["flow_id"]
        return AgentRunResult(ok=True, output="crew", iterations=1)

    monkeypatch.setattr(
        "orchestrator.agent.backends.crewai.backend.run_project_crew",
        fake_run_project_crew,
    )
    scope = JobScope(
        job_id="job-3",
        bridge=RecordingBridge(),
        role_ids=("project-manager", "recon"),
        extras={"role_id": "project-manager", "crew_mode": "project", "crew_flow_id": "flow-9"},
    )
    result = CrewAIBackend().start(_request(scope))
    assert result.output == "crew"
    assert captured["role_ids"] == ("recon",)
    assert captured["flow_id"] == "flow-9"


def test_project_crew_drops_the_requested_role_id_even_when_it_is_an_alias(monkeypatch):
    captured: dict = {}

    def fake_run_project_crew(scope, config, **kwargs):
        del scope, config
        captured["role_ids"] = kwargs["role_ids"]
        return AgentRunResult(ok=True, output="crew", iterations=1)

    monkeypatch.setattr(
        "orchestrator.agent.backends.crewai.backend.run_project_crew",
        fake_run_project_crew,
    )
    scope = JobScope(
        job_id="job-4",
        bridge=RecordingBridge(),
        role_ids=("osint-expert", "appsec"),
        extras={"role_id": "osint-expert", "crew_mode": "project"},
    )
    result = CrewAIBackend().start(_request(scope))
    assert result.ok is True
    assert captured["role_ids"] == ("appsec",)


def _request(scope: JobScope):
    from orchestrator.agent.runtime_base import AgentRunRequest

    return AgentRunRequest(scope=scope, config=AgentRunConfig())
