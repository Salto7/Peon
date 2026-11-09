"""Exact kwargs build_crew_agent passes to crewai.Agent. No live LLM call."""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator.crew.roles.catalog import DEFAULT_ROLES
from orchestrator.crew.roles.model import build_crew_agent
from orchestrator.crew.roles.registry import RoleRegistry

_FIXTURE = Path(__file__).parent / "fixtures" / "role_agent_kwargs.json"
_MODES = (
    ("default", {}),
    ("dispatch_only", {"dispatch_only": True}),
    ("collaborate", {"collaborate": True}),
)


class _RecordingAgent:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _reasoning_kwargs(kwargs: dict) -> dict:
    recorded: dict = {}
    if "planning_config" in kwargs:
        planning = kwargs["planning_config"]
        recorded["planning_config"] = {
            "reasoning_effort": planning.reasoning_effort,
            "max_attempts": planning.max_attempts,
        }
    if "reasoning" in kwargs:
        recorded["reasoning"] = kwargs["reasoning"]
    if "max_reasoning_attempts" in kwargs:
        recorded["max_reasoning_attempts"] = kwargs["max_reasoning_attempts"]
    return recorded


def test_default_roles_agent_kwargs(monkeypatch):
    monkeypatch.setattr("crewai.Agent", _RecordingAgent)
    monkeypatch.setattr(
        "orchestrator.crew.roles.model.llm_id_for_crew",
        lambda: "",
    )
    built = {role.id: role for role in RoleRegistry().list_roles()}
    assert set(built) == {defn.id for defn in DEFAULT_ROLES}

    observed = []
    for defn in DEFAULT_ROLES:
        role = built[defn.id]
        for mode, flags in _MODES:
            agent = build_crew_agent(role, **flags)
            kwargs = agent.kwargs
            observed.append(
                {
                    "role_id": defn.id,
                    "mode": mode,
                    "backstory": kwargs["backstory"],
                    "tools": [tool.name for tool in kwargs["tools"]],
                    "max_iter": kwargs["max_iter"],
                    "allow_delegation": kwargs["allow_delegation"],
                    "reasoning": _reasoning_kwargs(kwargs),
                }
            )

    expected = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert observed == expected
