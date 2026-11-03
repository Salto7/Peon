"""Draft a workflow from a goal: the LLM proposes, the catalog constrains, the linter judges.

Pipeline: catalog digest → LLM draft → normalise to a graph (real refs only) → structural repair (one root,
skills under roles that may use them, no cycles) → lint → if it still has errors, ask once more with the errors →
layout. The result is what a person would otherwise wire by hand, and it only leaves here if it can be compiled.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Callable

from orchestrator.workflow.compiler import WorkflowCompileError, compile_workflow
from orchestrator.workflow.graph import (
    ALLOWED_LINKS,
    auto_layout,
    find_cycle,
    parse_graph,
)
from orchestrator.workflow.linter import WorkflowLinter

SYSTEM = """You design executable multi-agent workflows from a catalog. Reply with JSON only.

A workflow is a hierarchy:
- ONE root: the project-manager role. Specialist roles hang under it (link kind "sequence" = runs after its parent
  finished, "parallel" = runs beside its sibling, both start once the same upstream finished).
- Skills hang under the role that uses them (a skill may only be attached to a role it is bound to in the catalog).
- Tools (CLIs) hang under the role or skill that needs them.
- Finish with the analyzer role after the specialists (sequence), so a report is produced.
Use ONLY ids from the catalog. Each role node needs an "instruction" (what exactly to do, concrete and verifiable,
may use {{seed_key}} placeholders) and an "acceptance" (how we know it is done). Ask the operator for what the
workflow cannot know (targets, files, constraints) through "seed_inputs" and reference them as {{key}}.

JSON shape:
{"name": str, "description": str,
 "seed_inputs": [{"key": str, "label": str, "kind": "text"|"scope", "required": bool,
                  "maps_to": "summary"|"scope", "target_type": "domain"|"ip"|"netblock"|"url"|"email"|"person"|"org"|"path"|"other"}],
 "nodes": [{"key": str, "kind": "role"|"skill"|"tool", "ref_id": str, "title": str?, "instruction": str?, "acceptance": str?}],
 "edges": [{"from": key, "to": key, "kind": "sequence"|"parallel"}]}"""

TARGET_TYPES = {"domain", "ip", "netblock", "url", "email", "person", "org", "path", "other"}


def catalog_digest() -> dict[str, Any]:
    from orchestrator.crew.roles.registry import RoleRegistry
    from orchestrator.skills.registry import SkillRegistry
    from orchestrator.tools.catalog import ToolCatalog

    roles, skills = RoleRegistry.shared(), SkillRegistry.shared()
    role_rows = []
    for r in roles.list_roles():
        role_rows.append(
            {
                "id": r.id,
                "kind": "manager" if r.is_manager else "analyzer" if r.is_analyzer else "specialist",
                "goal": (r.goal or "")[:200],
                "skills": list(skills.skills_for_role(r.id))[:12],
            }
        )
    skill_rows = [
        {"id": s.id, "about": (s.description or "")[:160], "roles": list(s.role_ids or ())}
        for s in skills.list_skills()
    ]
    tool_rows = [{"id": t["id"], "about": t["description"][:120]} for t in ToolCatalog.shared().summaries(limit=80)]
    return {"roles": role_rows, "skills": skill_rows, "tools": tool_rows}


def _prompt(goal: str, digest: dict[str, Any], feedback: list[str]) -> str:
    def block(title: str, rows: list[str]) -> str:
        return f"## {title}\n" + ("\n".join(rows) or "(none)")

    text = "\n\n".join(
        [
            f"GOAL:\n{goal.strip()}",
            block("Roles", [f"- {r['id']} ({r['kind']}): {r['goal']} | skills: {', '.join(r['skills']) or '-'}"
                            for r in digest["roles"]]),
            block("Skills", [f"- {s['id']}: {s['about']} | roles: {', '.join(s['roles']) or '-'}" for s in digest["skills"]]),
            block("Tools", [f"- {t['id']}: {t['about']}" for t in digest["tools"]]),
        ]
    )
    if feedback:
        text += "\n\nYour previous draft was rejected for these reasons — fix them:\n" + "\n".join(f"- {e}" for e in feedback)
    return text


def _text(value: Any, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _normalise(draft: Any, digest: dict[str, Any]) -> dict[str, Any]:
    """LLM JSON → designer document, dropping everything that does not exist in the catalog."""
    draft = draft if isinstance(draft, dict) else {}
    known = {
        "role": {r["id"] for r in digest["roles"]},
        "skill": {s["id"] for s in digest["skills"]},
        "tool": {t["id"] for t in digest["tools"]},
    }
    nodes: list[dict[str, Any]] = []
    by_key: dict[str, str] = {}
    for raw in draft.get("nodes") or []:
        raw = raw if isinstance(raw, dict) else {}
        kind, ref = _text(raw.get("kind"), 16).lower(), _text(raw.get("ref_id"), 128)
        key = _text(raw.get("key"), 64) or f"k{len(nodes)}"
        if kind not in known or ref not in known[kind] or key in by_key:
            continue
        node_id = f"n{uuid.uuid4().hex[:8]}"
        by_key[key] = node_id
        cfg: dict[str, Any] = {}
        if kind == "role":
            for field in ("title", "instruction", "acceptance"):
                if _text(raw.get(field)):
                    cfg[field] = _text(raw.get(field))
        nodes.append({"id": node_id, "kind": kind, "ref_id": ref, "label": ref, "x": 0, "y": 0, "config": cfg})
    edges: list[dict[str, Any]] = []
    kinds = {n["id"]: n["kind"] for n in nodes}
    seen: set[tuple[str, str]] = set()
    for raw in draft.get("edges") or []:
        raw = raw if isinstance(raw, dict) else {}
        s, t = by_key.get(_text(raw.get("from"), 64)), by_key.get(_text(raw.get("to"), 64))
        if not s or not t or s == t or (s, t) in seen or (kinds[s], kinds[t]) not in ALLOWED_LINKS:
            continue
        kind = "parallel" if _text(raw.get("kind"), 16).lower() == "parallel" and (kinds[s], kinds[t]) == ("role", "role") else "sequence"
        seen.add((s, t))
        edges.append({"id": f"e{uuid.uuid4().hex[:8]}", "source": s, "target": t, "kind": kind})
    seeds: list[dict[str, Any]] = []
    for raw in draft.get("seed_inputs") or []:
        raw = raw if isinstance(raw, dict) else {}
        key = re.sub(r"[^A-Za-z0-9_\-]+", "_", _text(raw.get("key"), 48)).strip("_")
        if not key or any(s["key"] == key for s in seeds):
            continue
        maps_to = "scope" if _text(raw.get("maps_to")).lower() == "scope" else "summary"
        target = _text(raw.get("target_type")).lower()
        seeds.append(
            {
                "key": key,
                "label": _text(raw.get("label"), 120) or key,
                "kind": "scope" if maps_to == "scope" else "text",
                "required": bool(raw.get("required")),
                "maps_to": maps_to,
                "target_type": target if target in TARGET_TYPES else "domain",
            }
        )
    return {
        "name": _text(draft.get("name"), 120),
        "description": _text(draft.get("description"), 600),
        "seed_inputs": seeds,
        "graph": {"nodes": nodes, "edges": edges},
    }


def _repair(doc: dict[str, Any], digest: dict[str, Any]) -> dict[str, Any]:
    """Make the structure valid where that is unambiguous: cycles, orphan skills, extra roots."""
    graph = doc["graph"]
    role_skills = {r["id"]: set(r["skills"]) for r in digest["roles"]}
    manager = next((r["id"] for r in digest["roles"] if r["kind"] == "manager"), "")

    # cycles: drop the edge that closes one
    for _ in range(len(graph["edges"]) + 1):
        g = parse_graph(graph)
        cycle = find_cycle(g)
        if not cycle:
            break
        a, b = cycle[-2], cycle[-1]
        graph["edges"] = [e for e in graph["edges"] if not (e["source"] == a and e["target"] == b)]

    g = parse_graph(graph)
    roles = [n for n in g.nodes if g.kind(n) == "role"]
    # skills: keep under an owner that may use them, else re-home, else drop
    drop: set[str] = set()
    for sid in [n for n in g.nodes if g.kind(n) == "skill"]:
        ref = g.ref(sid)
        owners = [p for p in g.incoming.get(sid, []) if g.kind(p) == "role"]
        ok = [p for p in owners if ref in role_skills.get(g.ref(p), set())]
        if not ok:
            home = next((r for r in roles if ref in role_skills.get(g.ref(r), set())), None)
            graph["edges"] = [e for e in graph["edges"] if e["target"] != sid]
            if home:
                graph["edges"].append({"id": f"e{uuid.uuid4().hex[:8]}", "source": home, "target": sid, "kind": "sequence"})
            else:
                drop.add(sid)
        elif len(ok) > 1:
            graph["edges"] = [e for e in graph["edges"] if not (e["target"] == sid and e["source"] != ok[0])]
    if drop:
        graph["nodes"] = [n for n in graph["nodes"] if n["id"] not in drop]
        graph["edges"] = [e for e in graph["edges"] if e["source"] not in drop and e["target"] not in drop]

    # one root: hang every other root under the manager (or the first role)
    g = parse_graph(graph)
    roots = g.roots()
    if len(roots) > 1:
        head = next((n for n in roots if g.kind(n) == "role" and g.ref(n) == manager), None) \
            or next((n for n in roots if g.kind(n) == "role"), None)
        for r in roots:
            if r == head or head is None:
                continue
            if (g.kind(head), g.kind(r)) in ALLOWED_LINKS:
                graph["edges"].append({"id": f"e{uuid.uuid4().hex[:8]}", "source": head, "target": r, "kind": "sequence"})
            else:
                graph["nodes"] = [n for n in graph["nodes"] if n["id"] != r]
    doc["graph"] = auto_layout(graph)
    return doc


def _finalize(doc: dict[str, Any], *, attempts: int = 1) -> dict[str, Any]:
    lint = WorkflowLinter.lint(graph=doc["graph"], seed_inputs=doc["seed_inputs"])
    plan: dict[str, Any] | None = None
    try:
        compiled = compile_workflow(
            graph=doc["graph"],
            seed_inputs=doc["seed_inputs"],
            name=doc["name"],
            description=doc["description"],
        )
        plan = {"objectives": compiled.preview(), "role_ids": compiled.role_ids}
    except WorkflowCompileError:
        plan = None
    return {
        **doc,
        "name": doc["name"] or "Generated workflow",
        "lint": lint,
        "plan": plan,
        "attempts": attempts,
    }


def generate_workflow(goal: str, *, chat: Callable[[str, str], dict] | None = None) -> dict[str, Any]:
    """Draft a workflow for ``goal``. Returns ``{name, description, seed_inputs, graph, lint, plan, attempts}``."""
    goal = (goal or "").strip()
    if len(goal) < 8:
        raise ValueError("describe the goal in a sentence or two")
    if chat is None:
        from orchestrator.utils import llm_usage
        from orchestrator.utils.llm import chat_json, require_llm

        require_llm()

        def chat(system: str, human: str) -> dict:
            with llm_usage.purpose("workflow_generate"):
                return chat_json(system, human)

    digest = catalog_digest()
    feedback: list[str] = []
    doc: dict[str, Any] = {}
    lint: dict[str, list[str]] = {"errors": [], "warnings": []}
    attempts = 0
    for attempts in (1, 2):
        doc = _repair(_normalise(chat(SYSTEM, _prompt(goal, digest, feedback)), digest), digest)
        lint = WorkflowLinter.lint(graph=doc["graph"], seed_inputs=doc["seed_inputs"])
        if not lint["errors"]:
            break
        feedback = lint["errors"]
    return _finalize(doc, attempts=attempts)


def _project_goal_text(
    *,
    title: str,
    summary: str,
    objectives: list[dict[str, Any]],
) -> str:
    lines = [
        f"Reconstruct a reusable preset workflow that would have produced this engagement.",
        f"Project: {title}",
    ]
    if summary:
        lines.append(f"Brief: {summary.strip()[:800]}")
    if objectives:
        lines.append("Objectives (preserve role order and intent):")
        for o in objectives:
            bits = [f"OBJ-{o.get('seq')}: {o.get('title') or '(untitled)'}"]
            if o.get("role_id"):
                bits.append(f"role={o['role_id']}")
            skills = o.get("skill_ids") or []
            if skills:
                bits.append("skills=" + ",".join(str(s) for s in skills[:8]))
            if o.get("description"):
                bits.append(str(o["description"])[:240])
            if o.get("acceptance"):
                bits.append(f"done when: {str(o['acceptance'])[:160]}")
            lines.append("- " + " | ".join(bits))
    else:
        lines.append("No objectives recorded — invent a sensible hierarchy for the brief.")
    return "\n".join(lines)


def _draft_from_objectives(
    *,
    title: str,
    summary: str,
    objectives: list[dict[str, Any]],
    digest: dict[str, Any],
) -> dict[str, Any] | None:
    """Deterministic graph from project objectives when roles are present; None if too thin."""
    known_roles = {r["id"] for r in digest["roles"]}
    known_skills = {s["id"] for s in digest["skills"]}
    role_skills = {r["id"]: set(r["skills"]) for r in digest["roles"]}
    manager = next((r["id"] for r in digest["roles"] if r["kind"] == "manager"), "")
    analyzer = next((r["id"] for r in digest["roles"] if r["kind"] == "analyzer"), "")

    ordered: list[str] = []
    instructions: dict[str, list[str]] = {}
    acceptances: dict[str, list[str]] = {}
    skills_for: dict[str, list[str]] = {}
    for o in objectives:
        rid = str(o.get("role_id") or "").strip()
        if not rid or rid not in known_roles:
            continue
        if rid not in ordered:
            ordered.append(rid)
            instructions[rid] = []
            acceptances[rid] = []
            skills_for[rid] = []
        title_bit = str(o.get("title") or "").strip()
        desc = str(o.get("description") or "").strip()
        if title_bit or desc:
            instructions[rid].append(": ".join(p for p in (title_bit, desc) if p)[:500])
        acc = str(o.get("acceptance") or "").strip()
        if acc:
            acceptances[rid].append(acc[:300])
        for sid in o.get("skill_ids") or []:
            sid = str(sid).strip()
            if sid in known_skills and sid in role_skills.get(rid, set()) and sid not in skills_for[rid]:
                skills_for[rid].append(sid)

    if not ordered:
        return None

    # Bookend with manager / analyzer when the catalog has them.
    if manager and manager not in ordered:
        ordered.insert(0, manager)
        instructions.setdefault(manager, [])
        acceptances.setdefault(manager, [])
        skills_for.setdefault(manager, [])
    elif manager and ordered[0] != manager:
        ordered = [manager] + [r for r in ordered if r != manager]
    if analyzer and analyzer not in ordered:
        ordered.append(analyzer)
        instructions.setdefault(analyzer, [])
        acceptances.setdefault(analyzer, [])
        skills_for.setdefault(analyzer, [])
    elif analyzer and ordered[-1] != analyzer:
        ordered = [r for r in ordered if r != analyzer] + [analyzer]

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    role_keys: dict[str, str] = {}
    for rid in ordered:
        key = f"role_{rid}"
        role_keys[rid] = key
        instr = "\n\n".join(instructions.get(rid) or []) or (
            f"Coordinate the engagement." if rid == manager else
            f"Produce the engagement report." if rid == analyzer else
            f"Carry out the {rid} work for this engagement."
        )
        nodes.append(
            {
                "key": key,
                "kind": "role",
                "ref_id": rid,
                "instruction": instr,
                "acceptance": "\n".join(acceptances.get(rid) or []) or "Step complete and evidence recorded.",
            }
        )
        for i, sid in enumerate(skills_for.get(rid) or []):
            sk = f"skill_{rid}_{sid}"
            nodes.append({"key": sk, "kind": "skill", "ref_id": sid})
            edges.append({"from": key, "to": sk, "kind": "sequence" if i == 0 else "parallel"})

    for a, b in zip(ordered, ordered[1:]):
        edges.append({"from": role_keys[a], "to": role_keys[b], "kind": "sequence"})

    seeds = [
        {
            "key": "target",
            "label": "Primary target",
            "kind": "scope",
            "required": True,
            "maps_to": "scope",
            "target_type": "domain",
        }
    ]
    return {
        "name": (title or "From project").strip()[:120],
        "description": (summary or f"Deduced from project “{title}”.").strip()[:600],
        "seed_inputs": seeds,
        "nodes": nodes,
        "edges": edges,
    }


def deduce_workflow_from_project(project_id: str, *, chat: Callable[[str, str], dict] | None = None) -> dict[str, Any]:
    """Build a draft workflow from an existing project's plan (objectives + brief).

    Prefer a deterministic reconstruction from objective roles/skills; if the project
    has no usable roles, fall back to the LLM generator with a project-derived goal.
    """
    from peon.projects.db import ensure_project_database, using_project
    from peon.projects.models import Objective, Project

    pid = str(project_id or "").strip()
    if not pid:
        raise ValueError("project_id is required")
    project = Project.objects.filter(pk=pid).first()
    if project is None:
        raise ValueError(f"unknown project {pid!r}")

    ensure_project_database(pid)
    with using_project(pid):
        objectives = [
            {
                "seq": o.seq,
                "title": o.title,
                "description": o.description,
                "acceptance": o.acceptance_criteria,
                "role_id": o.role_id,
                "skill_ids": list(o.skill_ids or []),
            }
            for o in Objective.objects.filter(project_id=pid).order_by("seq", "created_at")[:60]
        ]

    digest = catalog_digest()
    draft = _draft_from_objectives(
        title=project.title,
        summary=project.summary or "",
        objectives=objectives,
        digest=digest,
    )
    if draft is not None:
        doc = _repair(_normalise(draft, digest), digest)
        return _finalize(doc, attempts=0)

    goal = _project_goal_text(title=project.title, summary=project.summary or "", objectives=objectives)
    return generate_workflow(goal, chat=chat)
