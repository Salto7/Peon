"""Catalog, alias, and capability-tag characterization. No behavior change."""

from __future__ import annotations

from orchestrator.crew.tools.catalog import (
    canonicalize_crew_tools,
    for_dispatch_only,
    known_tool_names,
    tool_tags,
    with_collab_tools,
)

_KNOWN_TOOLS = (
    "ask_operator",
    "assert_in_scope",
    "bash",
    "call_mcp_tool",
    "check_inbox",
    "explore_branches",
    "forget_memory",
    "get_credential",
    "go_idle",
    "list_credentials",
    "list_delegations",
    "list_findings",
    "list_mcp_resources",
    "list_mcp_tools",
    "list_objectives",
    "list_roles",
    "list_team",
    "list_work",
    "list_workspace_artifacts",
    "load_skill",
    "message_status",
    "propose_work",
    "provision_cli",
    "read_mcp_resource",
    "read_skill_resource",
    "read_workspace_artifact",
    "recall_memory",
    "record_assets",
    "record_finding",
    "record_findings",
    "record_intent",
    "record_observation",
    "roe_status",
    "run_periodic",
    "run_skill_script",
    "sandbox_status",
    "save_memory",
    "search_evidence",
    "send_message",
    "spawn_work",
    "update_objective_status",
    "wait_for_work",
    "write_report_note",
    "write_workspace_artifact",
)

_ANALYZER_TOOLS = (
    "list_objectives",
    "update_objective_status",
    "record_finding",
    "record_findings",
    "record_assets",
    "list_findings",
    "list_team",
    "send_message",
    "check_inbox",
    "message_status",
    "recall_memory",
    "save_memory",
    "forget_memory",
    "list_credentials",
    "get_credential",
    "list_workspace_artifacts",
    "read_workspace_artifact",
    "write_report_note",
)

_MANAGER_TOOLS = (
    "sandbox_status",
    "provision_cli",
    "bash",
    "explore_branches",
    "run_periodic",
    "roe_status",
    "assert_in_scope",
    "list_objectives",
    "update_objective_status",
    "list_findings",
    "list_roles",
    "spawn_work",
    "wait_for_work",
    "list_work",
    "propose_work",
    "ask_operator",
    "recall_memory",
    "save_memory",
    "forget_memory",
    "list_credentials",
    "get_credential",
    "list_team",
    "send_message",
    "check_inbox",
    "message_status",
    "go_idle",
    "list_delegations",
)

_TECHNICAL_TOOLS = (
    "sandbox_status",
    "provision_cli",
    "bash",
    "explore_branches",
    "run_periodic",
    "roe_status",
    "assert_in_scope",
    "list_objectives",
    "update_objective_status",
    "record_finding",
    "record_findings",
    "record_assets",
    "list_findings",
    "list_mcp_tools",
    "call_mcp_tool",
    "list_mcp_resources",
    "read_mcp_resource",
    "list_roles",
    "spawn_work",
    "wait_for_work",
    "list_work",
    "propose_work",
    "ask_operator",
    "recall_memory",
    "save_memory",
    "forget_memory",
    "list_credentials",
    "get_credential",
    "list_team",
    "send_message",
    "check_inbox",
    "message_status",
    "go_idle",
)


class _Role:
    def __init__(self, kind: str, tools: tuple[str, ...] = ()) -> None:
        self.kind = kind
        self.tools = tools
        self.is_analyzer = kind == "analyzer"
        self.is_manager = kind == "engagement-pm"
        self.is_technical = kind == "technical"


def test_known_tool_names():
    assert tuple(known_tool_names()) == _KNOWN_TOOLS


def test_alias_canonicalization():
    assert canonicalize_crew_tools(
        [
            "spawn_agent",
            "wait_for_agents",
            "propose_agents",
            "list_agents",
            "sandbox_setup",
            "skills_list",
            "skill_view",
            "bash",
            "bash",
            "",
            "  ",
            "not_a_tool",
        ]
    ) == (
        "spawn_work",
        "wait_for_work",
        "propose_work",
        "list_work",
        "sandbox_status",
        "bash",
        "not_a_tool",
    )
    assert canonicalize_crew_tools(None) == ()
    assert canonicalize_crew_tools([]) == ()


def test_with_collab_tools_per_role_kind():
    assert with_collab_tools(_Role("analyzer")) == _ANALYZER_TOOLS
    assert with_collab_tools(_Role("engagement-pm")) == _MANAGER_TOOLS
    assert with_collab_tools(_Role("technical")) == _TECHNICAL_TOOLS
    assert with_collab_tools(_Role("other", ("spawn_agent", "bash", "bash"))) == (
        "spawn_work",
        "bash",
    )


def test_for_dispatch_only():
    assert for_dispatch_only(
        [
            "bash",
            "list_findings",
            "explore_branches",
            "run_skill_script",
            "run_periodic",
            "provision_cli",
            "call_mcp_tool",
            "ask_operator",
        ]
    ) == (
        "list_findings",
        "ask_operator",
        "list_workspace_artifacts",
        "read_workspace_artifact",
        "write_workspace_artifact",
    )
    assert for_dispatch_only(
        ["list_findings", "read_workspace_artifact"]
    ) == (
        "list_findings",
        "read_workspace_artifact",
        "list_workspace_artifacts",
        "write_workspace_artifact",
    )


def test_bash_is_the_only_shell_tagged_tool():
    # commands.py treats a ``shell`` tag as the bash positional payload.
    assert [name for name in known_tool_names() if "shell" in tool_tags(name)] == [
        "bash"
    ]
    assert tool_tags("bash") == frozenset({"shell"})
    assert tool_tags("sandbox_status") == frozenset()
    assert tool_tags("not_a_tool") == frozenset()
