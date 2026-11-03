"""Validates one preset-workflow graph: real refs, valid hierarchy, and that it can actually run.

Errors block publishing; warnings are advice. The checks mirror what the compiler / scheduler need — a graph that
passes here compiles to a plan the objective scheduler can execute.
"""

from __future__ import annotations

import re
from typing import Any

from orchestrator.utils.service import SharedServiceBase
from orchestrator.workflow.graph import (
    ALLOWED_LINKS,
    EDGE_KINDS,
    NODE_KINDS,
    SEED_PLACEHOLDER,
    Graph,
    find_cycle,
    parse_graph,
)


class WorkflowLinter(SharedServiceBase):
    @staticmethod
    def lint(*, graph: dict[str, Any], seed_inputs: list[Any]) -> dict[str, list[str]]:
        """``{"errors": [...], "warnings": [...]}`` — only ``errors`` blocks publishing."""
        g = parse_graph(graph)
        errors: list[str] = list(g.problems)
        warnings: list[str] = []

        errors.extend(_check_refs(g))
        errors.extend(_check_links(g))

        if g.nodes:
            roots = g.roots()
            if len(roots) > 1:
                errors.append("the workflow has more than one root (no incoming link): " + ", ".join(sorted(roots))
                              + " — connect them under one role (usually the project manager)")
            elif not roots:
                errors.append("the workflow has no root — every node has an incoming link")
            elif g.kind(roots[0]) != "role":
                errors.append(f"the root {roots[0]!r} must be a role (a skill or tool cannot start a workflow)")
        else:
            errors.append("the workflow is empty — add a role to start")

        cycle = find_cycle(g)
        if cycle:
            errors.append("the workflow has a cycle: " + " -> ".join(cycle))

        errors.extend(_check_skill_ownership(g))
        warnings.extend(_check_quality(g))
        seed_errors, seed_warnings = _check_seed_inputs(g, seed_inputs)
        errors.extend(seed_errors)
        warnings.extend(seed_warnings)
        return {"errors": errors, "warnings": warnings}


def _catalogs():
    from orchestrator.crew.roles.registry import RoleRegistry
    from orchestrator.skills.registry import SkillRegistry
    from orchestrator.tools.catalog import ToolCatalog

    return RoleRegistry.shared(), SkillRegistry.shared(), ToolCatalog.shared()


def _check_refs(g: Graph) -> list[str]:
    roles, skills, tools = _catalogs()
    errors: list[str] = []
    for node_id, node in g.nodes.items():
        kind = str(node.get("kind") or "").strip()
        ref_id = str(node.get("ref_id") or "").strip()
        if kind not in NODE_KINDS:
            errors.append(f"node {node_id!r} has unknown kind {kind!r}")
        elif not ref_id:
            errors.append(f"node {node_id!r} ({kind}) has no catalog item")
        elif kind == "role" and roles.get(ref_id) is None:
            errors.append(f"node {node_id!r} references unknown role {ref_id!r}")
        elif kind == "skill" and skills.get(ref_id) is None:
            errors.append(f"node {node_id!r} references unknown skill {ref_id!r}")
        elif kind == "tool" and tools.by_id(ref_id) is None:
            errors.append(f"node {node_id!r} references unknown tool {ref_id!r}")
    return errors


def _label(g: Graph, node_id: str) -> str:
    return f"{g.kind(node_id)} {g.ref(node_id) or node_id!r}"


def _check_links(g: Graph) -> list[str]:
    errors: list[str] = []
    for e in g.edges:
        s, t, kind = e["source"], e["target"], e["kind"]
        if g.kind(s) not in NODE_KINDS or g.kind(t) not in NODE_KINDS:
            continue   # reported as an unknown kind
        if kind not in EDGE_KINDS:
            errors.append(f"link {_label(g, s)} -> {_label(g, t)} has unknown kind {kind!r}")
        if (g.kind(s), g.kind(t)) not in ALLOWED_LINKS:
            errors.append(f"{_label(g, s)} cannot connect to {_label(g, t)} — links go role→role, role→skill, "
                          "role→tool or skill→tool")
        elif kind == "parallel" and (g.kind(s), g.kind(t)) != ("role", "role"):
            errors.append(f"only role→role links can be parallel ({_label(g, s)} -> {_label(g, t)})")
    return errors


def _check_skill_ownership(g: Graph) -> list[str]:
    """A skill belongs to exactly one role, and that role must be allowed to use it."""
    _, skills, _ = _catalogs()
    errors: list[str] = []
    for node_id in g.nodes:
        if g.kind(node_id) != "skill":
            continue
        owners = [p for p in g.incoming.get(node_id, []) if g.kind(p) == "role"]
        if len(owners) > 1:
            errors.append(f"{_label(g, node_id)} is attached to {len(owners)} roles — use one skill node per role")
        for owner in owners:
            if g.ref(owner) and g.ref(node_id) and skills.usable(g.ref(node_id), role_id=g.ref(owner)) is None:
                errors.append(f"{_label(g, node_id)} is not bound to {_label(g, owner)} — bind it in the Catalog "
                              "or move it under a role that can use it")
    return errors


def _check_quality(g: Graph) -> list[str]:
    roles, _, _ = _catalogs()
    warnings: list[str] = []
    role_nodes = [n for n in g.nodes if g.kind(n) == "role"]
    specs = {n: roles.get(g.ref(n)) for n in role_nodes}
    if not any(s is not None and s.is_manager for s in specs.values()):
        warnings.append("no project-manager role: one is added as the first objective")
    elif (roots := g.roots()) and len(roots) == 1 and specs.get(roots[0]) is not None and not specs[roots[0]].is_manager:
        warnings.append(f"the root is {g.ref(roots[0])!r}, not the project manager: a manager objective is added first")
    if not any(s is not None and s.is_analyzer for s in specs.values()):
        warnings.append("no analyzer role: a reporting objective is added last")
    for n in role_nodes:
        spec = specs[n]
        if spec is None or spec.is_manager or spec.is_analyzer:
            continue
        if not g.children(n, "skill"):
            warnings.append(f"role {g.ref(n)!r} has no skill attached — it will rely on its default toolbox")
        cfg = g.nodes[n].get("config") if isinstance(g.nodes[n].get("config"), dict) else {}
        if not str(cfg.get("instruction") or "").strip():
            warnings.append(f"role {g.ref(n)!r} has no instruction — it only gets the role's generic goal")
    for e in g.edges:
        if e["kind"] == "parallel" and not [p for p in g.incoming.get(e["source"], []) if g.kind(p) == "role"]:
            warnings.append(f"parallel link from {_label(g, e['source'])}: it has nothing upstream, so it "
                            "behaves like a normal link")
    for n in role_nodes:
        if specs[n] is not None and specs[n].is_analyzer and g.children(n, "role"):
            warnings.append(f"role {g.ref(n)!r} (analyzer) has roles after it — the report is always the last "
                            "objective, so they will not wait for it")
    if len(g.nodes) == 1:
        warnings.append("the workflow is a single node — connect skills and other roles to make it useful")
    return warnings


def _node_text(node: dict[str, Any]) -> str:
    cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
    return " ".join(str(v) for v in cfg.values() if isinstance(v, str))


def _check_seed_inputs(g: Graph, seed_inputs: list[Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    keys: set[str] = set()
    for item in seed_inputs if isinstance(seed_inputs, list) else []:
        key = str((item or {}).get("key") or "").strip()
        if not key:
            warnings.append("a seed input is missing a key")
        elif not re.fullmatch(r"[A-Za-z0-9_\-]+", key):
            errors.append(f"seed input key {key!r} may only use letters, digits, '_' and '-'")
        elif key in keys:
            errors.append(f"duplicate seed input key {key!r}")
        else:
            keys.add(key)
    used: set[str] = set()
    for node_id, node in g.nodes.items():
        cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
        for key in re.findall(SEED_PLACEHOLDER, _node_text(node)):
            used.add(key)
            if key not in keys:
                errors.append(f"{_label(g, node_id)} uses {{{{{key}}}}} but no seed input has that key")
        used.update(str(k) for k, v in cfg.items() if v is True)   # legacy: a node "consumes" a seed input
    for key in sorted(keys - used):
        warnings.append(f"seed input {key!r} is not used by any node — reference it as {{{{{key}}}}} in an instruction")
    return errors, warnings
