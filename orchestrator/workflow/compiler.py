"""Compile a designer graph into the plan the objective scheduler runs.

The output has exactly the shape the project planner produces (``objectives_payload`` for
``persist_project_plan``): one objective per role node, in dependency order, with the skills / CLI tools
that hang under the node and ``depends_on`` derived from the role→role links. A published workflow therefore
needs no LLM planning at project creation — the graph *is* the plan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from orchestrator.workflow.graph import SEED_PLACEHOLDER, Graph, parse_graph, topo_order
from orchestrator.workflow.linter import WorkflowLinter

#: Objective phases the scheduler knows (see ``KillChainPhase``).
PHASES = ("recon", "initial-access", "post-exploit", "reporting")

DEFAULT_ACCEPTANCE = (
    "The objective is finished with its evidence recorded (findings, artifacts) and its status updated."
)


class WorkflowCompileError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("workflow cannot run: " + "; ".join(errors))
        self.errors = errors


@dataclass
class CompiledPlan:
    objectives: list[dict[str, Any]] = field(default_factory=list)
    plan_text: str = ""
    warnings: list[str] = field(default_factory=list)
    role_ids: list[str] = field(default_factory=list)

    def preview(self) -> list[dict[str, Any]]:
        """What the designer shows: one row per objective with its dependencies by position."""
        return [
            {
                "seq": i,
                "title": o["title"],
                "role_id": o["role_id"],
                "skill_ids": o.get("skill_ids", []),
                "tools": o.get("tools", []),
                "depends_on": o.get("depends_on", []),
                "phase": o.get("phase", ""),
            }
            for i, o in enumerate(self.objectives, start=1)
        ]


def _fill(text: str, answers: dict[str, str]) -> str:
    return re.sub(SEED_PLACEHOLDER, lambda m: answers.get(m.group(1), m.group(0)), text or "")


def _canvas_order(g: Graph, ids: list[str]) -> list[str]:
    return sorted(ids, key=lambda n: (float(g.nodes[n].get("y") or 0), float(g.nodes[n].get("x") or 0), n))


def compile_workflow(
    *,
    graph: dict[str, Any],
    seed_inputs: list[Any] | None = None,
    answers: dict[str, str] | None = None,
    name: str = "",
    description: str = "",
    lint: bool = True,
) -> CompiledPlan:
    """Graph → plan. Raises :class:`WorkflowCompileError` when the graph cannot run (lint errors)."""
    seeds = [s for s in (seed_inputs or []) if isinstance(s, dict)]
    answers = {k: str(v) for k, v in (answers or {}).items() if str(v).strip()}
    report = WorkflowLinter.lint(graph=graph, seed_inputs=seeds) if lint else {"errors": [], "warnings": []}
    if report["errors"]:
        raise WorkflowCompileError(report["errors"])

    from orchestrator.crew.roles.registry import RoleRegistry
    from orchestrator.planning.planners.project import bookend_project_objectives

    g = parse_graph(graph)
    registry = RoleRegistry.shared()
    labels = {str(s.get("key")): str(s.get("label") or s.get("key")) for s in seeds}
    order = topo_order(g, "role")
    position = {n: i for i, n in enumerate(order, start=1)}

    # role → set of upstream role nodes it waits for
    waits: dict[str, set[str]] = {}
    for n in order:
        deps: set[str] = set()
        for e in g.edges:
            if e["target"] != n or g.kind(e["source"]) != "role":
                continue
            if e["kind"] == "parallel":
                deps |= waits.get(e["source"], set())   # runs beside the source: same upstream, not the source
            else:
                deps.add(e["source"])
        waits[n] = deps

    objectives: list[dict[str, Any]] = []
    titles: dict[str, int] = {}
    bookend_nodes: dict[str, dict[str, Any]] = {}   # manager / analyzer nodes: merged into the bookends below
    for n in order:
        spec = registry.get(g.ref(n))
        label = (spec.label if spec else g.ref(n)) or g.ref(n)
        cfg = g.nodes[n].get("config") if isinstance(g.nodes[n].get("config"), dict) else {}
        skill_nodes = _canvas_order(g, g.children(n, "skill"))
        skills = list(dict.fromkeys(g.ref(s) for s in skill_nodes))
        tool_nodes = g.children(n, "tool") + [t for s in skill_nodes for t in g.children(s, "tool")]
        tools = list(dict.fromkeys(g.ref(t) for t in tool_nodes))

        instruction = _fill(str(cfg.get("instruction") or "").strip(), answers)
        for key, value in answers.items():   # legacy toggle: the node "consumes" a seed input
            if cfg.get(key) is True and f"{{{{{key}}}}}" not in str(cfg.get("instruction") or ""):
                instruction += f"\nOperator input — {labels.get(key, key)}: {value}"
        if not instruction:
            instruction = (spec.goal if spec and spec.goal else f"Carry out the {label} step.").strip()
        parts = [instruction.strip()]
        if skills:
            parts.append("Skills for this step: " + ", ".join(skills) + ". Load them with load_skill and follow them.")
        if tools:
            parts.append("CLI tools this step relies on: " + ", ".join(tools) + " (provision_cli installs them).")
        title = _fill(str(cfg.get("title") or "").strip(), answers) or (
            f"{label} · {', '.join(skills[:2])}" if skills else label
        )
        count = titles.get(title, 0) + 1
        titles[title] = count
        if count > 1:
            title = f"{title} ({count})"
        phase = str(cfg.get("phase") or "").strip().lower()
        if phase not in PHASES:   # the scheduler's phases; the manager and specialists default to the first
            phase = "reporting" if spec and spec.is_analyzer else PHASES[0]
        if spec and (spec.is_manager or spec.is_analyzer):
            bookend_nodes[spec.id] = {
                "instruction": str(cfg.get("instruction") or "").strip() and _fill(str(cfg.get("instruction")).strip(), answers),
                "acceptance": _fill(str(cfg.get("acceptance") or "").strip(), answers),
                "skills": skills,
                "tools": tools,
            }
        objectives.append(
            {
                "title": title[:255],
                "phase": phase,
                "description": "\n\n".join(parts),
                "acceptance_criteria": _fill(str(cfg.get("acceptance") or "").strip(), answers) or DEFAULT_ACCEPTANCE,
                "role_id": g.ref(n),
                "skill_ids": skills,
                "tools": tools,
                "depends_on": sorted(position[d] for d in waits[n]),
                "commands": [f'provision_cli("{t}")' for t in tools],
                "mitre": [],
            }
        )

    objectives = bookend_project_objectives(objectives)   # drops manager/analyzer rows and re-adds them first / last
    for row in objectives:   # …so what the designer said about those two roles is merged back in
        extra = bookend_nodes.get(row.get("role_id", ""))
        if not extra:
            continue
        if extra["instruction"]:
            row["description"] = f"{row.get('description', '')}\n\nWorkflow instruction: {extra['instruction']}".strip()
        if extra["acceptance"]:
            row["acceptance_criteria"] = extra["acceptance"]
        row["skill_ids"] = list(dict.fromkeys([*(row.get("skill_ids") or []), *extra["skills"]]))
        if extra["tools"]:
            row["tools"] = extra["tools"]
            row["commands"] = list(dict.fromkeys([*(row.get("commands") or []), *(f'provision_cli("{t}")' for t in extra["tools"])]))
    plan = CompiledPlan(
        objectives=objectives,
        warnings=list(report["warnings"]),
        role_ids=list(dict.fromkeys(o["role_id"] for o in objectives)),
    )
    plan.plan_text = _plan_text(name, description, objectives)
    return plan


def _plan_text(name: str, description: str, objectives: list[dict[str, Any]]) -> str:
    lines = [f"# Workflow: {name or 'preset'}", ""]
    if description:
        lines += [description.strip(), ""]
    for i, o in enumerate(objectives, start=1):
        deps = ", ".join(f"OBJ-{d}" for d in o.get("depends_on", [])) or "—"
        lines.append(f"{i}. **{o['title']}** — role `{o['role_id']}` · after: {deps}")
        if o.get("skill_ids"):
            lines.append(f"   - skills: {', '.join(o['skill_ids'])}")
        if o.get("tools"):
            lines.append(f"   - tools: {', '.join(o['tools'])}")
    return "\n".join(lines) + "\n"
