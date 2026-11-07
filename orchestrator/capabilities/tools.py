"""Capability names registered for command-helper lookups.

Only the ``(group, tags, name)`` triple is ever read back — by
``orchestrator.utils.commands`` (AST-parsed capability-call shapes). There is
no handler to run: each name below mirrors the live CrewAI tool of the same
name in ``orchestrator/crew/tools/adapters.py``, which is what actually runs.
"""

from __future__ import annotations

from orchestrator.capabilities.registry import REGISTRY, CapabilityGroup

_SANDBOX = CapabilityGroup.SANDBOX
_ENGAGEMENT = CapabilityGroup.ENGAGEMENT

# (name, group, tags) — ``shell`` tags drive orchestrator.utils.commands'
# shell-arg detection for ``bash(...)`` capability calls.
_CAPABILITIES: tuple[tuple[str, CapabilityGroup, frozenset[str]], ...] = (
    ("sandbox_status", _SANDBOX, frozenset()),
    ("provision_cli", _SANDBOX, frozenset()),
    ("bash", _SANDBOX, frozenset({"shell"})),
    ("run_periodic", _SANDBOX, frozenset()),
    ("list_objectives", _ENGAGEMENT, frozenset()),
    ("update_objective_status", _ENGAGEMENT, frozenset()),
    ("ask_operator", _ENGAGEMENT, frozenset()),
    ("record_finding", _ENGAGEMENT, frozenset()),
    ("record_findings", _ENGAGEMENT, frozenset()),
    ("recall_memory", _ENGAGEMENT, frozenset()),
    ("save_memory", _ENGAGEMENT, frozenset()),
    ("forget_memory", _ENGAGEMENT, frozenset()),
    ("list_credentials", _ENGAGEMENT, frozenset()),
    ("get_credential", _ENGAGEMENT, frozenset()),
    ("list_team", _ENGAGEMENT, frozenset()),
    ("send_message", _ENGAGEMENT, frozenset()),
    ("check_inbox", _ENGAGEMENT, frozenset()),
    ("go_idle", _ENGAGEMENT, frozenset()),
    ("record_assets", _ENGAGEMENT, frozenset()),
    ("list_findings", _ENGAGEMENT, frozenset()),
    ("search_evidence", _ENGAGEMENT, frozenset()),
    ("record_observation", _ENGAGEMENT, frozenset()),
    ("record_intent", _ENGAGEMENT, frozenset()),
)


for _name, _group, _tags in _CAPABILITIES:
    REGISTRY.register(_group, tags=_tags, name=_name)
