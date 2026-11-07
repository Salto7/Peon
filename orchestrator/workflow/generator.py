"""Draft a workflow from a goal: the LLM proposes, the catalog constrains, the linter judges.

Pipeline: catalog digest → LLM draft → normalise to a graph (real refs only) → structural repair (one root,
skills under roles that may use them, no cycles) → lint → if it still has errors, ask once more with the errors →
layout. The result is what a person would otherwise wire by hand, and it only leaves here if it can be compiled.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from typing import Any

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
                "kind": "manager"
                if r.is_manager
                else "analyzer"
                if r.is_analyzer
                else "specialist",
                "goal": (r.goal or "")[:200],
                "skills": list(skills.skills_for_role(r.id))[:12],
            }
        )
    skill_rows = [
        {"id": s.id, "about": (s.description or "")[:160], "roles": list(s.role_ids or ())}
        for s in skills.list_skills()
    ]
    tool_rows = [
        {"id": t["id"], "about": t["description"][:120]}
        for t in ToolCatalog.shared().summaries(limit=80)
    ]
    return {"roles": role_rows, "skills": skill_rows, "tools": tool_rows}


def _prompt(goal: str, digest: dict[str, Any], feedback: list[str], lessons: str = "") -> str:
    def block(title: str, rows: list[str]) -> str:
        return f"## {title}\n" + ("\n".join(rows) or "(none)")

    text = "\n\n".join(
        [
            f"GOAL:\n{goal.strip()}",
            block(
                "Roles",
                [
                    f"- {r['id']} ({r['kind']}): {r['goal']} | skills: {', '.join(r['skills']) or '-'}"
                    for r in digest["roles"]
                ],
            ),
            block(
                "Skills",
                [
                    f"- {s['id']}: {s['about']} | roles: {', '.join(s['roles']) or '-'}"
                    for s in digest["skills"]
                ],
            ),
            block("Tools", [f"- {t['id']}: {t['about']}" for t in digest["tools"]]),
        ]
    )
    if lessons.strip():
        # Already framed as unverified prior-engagement context (see
        # orchestrator.utils.governance.frame_recalled_lesson) — a hint
        # toward a sturdier graph/order, not a template to copy verbatim.
        text += "\n\n## Lessons from earlier engagements (may not apply here)\n" + lessons.strip()
    if feedback:
        text += "\n\nYour previous draft was rejected for these reasons — fix them:\n" + "\n".join(
            f"- {e}" for e in feedback
        )
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
            for field in ("title", "instruction", "acceptance", "phase"):
                val = _text(raw.get(field), 32 if field == "phase" else 4000)
                if val:
                    cfg[field] = val
        nodes.append(
            {
                "id": node_id,
                "kind": kind,
                "ref_id": ref,
                "label": ref,
                "x": 0,
                "y": 0,
                "config": cfg,
            }
        )
    edges: list[dict[str, Any]] = []
    kinds = {n["id"]: n["kind"] for n in nodes}
    seen: set[tuple[str, str]] = set()
    for raw in draft.get("edges") or []:
        raw = raw if isinstance(raw, dict) else {}
        s, t = by_key.get(_text(raw.get("from"), 64)), by_key.get(_text(raw.get("to"), 64))
        if not s or not t or s == t or (s, t) in seen or (kinds[s], kinds[t]) not in ALLOWED_LINKS:
            continue
        kind = (
            "parallel"
            if _text(raw.get("kind"), 16).lower() == "parallel"
            and (kinds[s], kinds[t]) == ("role", "role")
            else "sequence"
        )
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
                graph["edges"].append(
                    {
                        "id": f"e{uuid.uuid4().hex[:8]}",
                        "source": home,
                        "target": sid,
                        "kind": "sequence",
                    }
                )
            else:
                drop.add(sid)
        elif len(ok) > 1:
            graph["edges"] = [
                e for e in graph["edges"] if not (e["target"] == sid and e["source"] != ok[0])
            ]
    if drop:
        graph["nodes"] = [n for n in graph["nodes"] if n["id"] not in drop]
        graph["edges"] = [
            e for e in graph["edges"] if e["source"] not in drop and e["target"] not in drop
        ]

    # one root: hang every other root under the manager (or the first role)
    g = parse_graph(graph)
    roots = g.roots()
    if len(roots) > 1:
        head = next(
            (n for n in roots if g.kind(n) == "role" and g.ref(n) == manager), None
        ) or next((n for n in roots if g.kind(n) == "role"), None)
        for r in roots:
            if r == head or head is None:
                continue
            if (g.kind(head), g.kind(r)) in ALLOWED_LINKS:
                graph["edges"].append(
                    {
                        "id": f"e{uuid.uuid4().hex[:8]}",
                        "source": head,
                        "target": r,
                        "kind": "sequence",
                    }
                )
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


def generate_workflow(
    goal: str, *, chat: Callable[[str, str], dict] | None = None
) -> dict[str, Any]:
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
    from orchestrator.crew.memory import MemoryService

    # recall_for_new_project is already exception-safe end to end (a dead
    # memory backend returns "", never raises) — no guard needed here.
    lessons = MemoryService.recall_for_new_project(title=goal, top_k=5)
    feedback: list[str] = []
    doc: dict[str, Any] = {}
    lint: dict[str, list[str]] = {"errors": [], "warnings": []}
    attempts = 0
    for n in (1, 2):
        attempts = n
        doc = _repair(
            _normalise(chat(SYSTEM, _prompt(goal, digest, feedback, lessons)), digest), digest
        )
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
        "Reconstruct a reusable preset workflow that would have produced this engagement.",
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


def _seed_target_type_from_roe(in_scope: list[Any] | None) -> str:
    """Map the first in-scope target's type into a workflow seed ``target_type``."""
    aliases = {
        "fqdn": "domain",
        "hostname": "domain",
        "host": "domain",
        "dns": "domain",
        "ipv4": "ip",
        "ipv6": "ip",
        "cidr": "netblock",
        "organization": "org",
        "organisation": "org",
        "company": "org",
        "asset": "org",
        "file": "path",
        "filepath": "path",
    }

    def _map(raw: str) -> str:
        mapped = aliases.get(raw.strip().lower(), raw.strip().lower())
        return mapped if mapped in TARGET_TYPES else "domain"

    # Prefer structured RoE rows ({"type","value"}) without importing coercion.
    for item in in_scope or []:
        if isinstance(item, dict):
            raw = str(item.get("type") or item.get("asset_type") or "").strip()
            if raw:
                return _map(raw)
    try:
        from peon.projects.targets import coerce_targets

        scope = coerce_targets(in_scope or [])
    except Exception:
        return "domain"
    if not scope:
        return "domain"
    return _map(str(scope[0].get("type") or ""))


def _draft_from_objectives(
    *,
    title: str,
    summary: str,
    objectives: list[dict[str, Any]],
    digest: dict[str, Any],
    seed_target_type: str = "domain",
) -> dict[str, Any] | None:
    """Deterministic graph from project objectives when roles are present; None if too thin.

    Edge rules:
    - One role node per Objective (key ``role_{seq}_{role_id}``); never collapse by role_id.
    - If B.depends_on contains A → sequence edge A→B (objective dependency order).
    - Objectives that share the same dependency set become siblings: parallel edges from the
      earliest (by seq) to each peer so the designer shows fan-out without forcing a serial chain.
    - Skill nodes hang under that objective's role node (catalog-known ids only; binding checked in
      ``_repair``).
    - Manager / analyzer bookends are inserted only when missing from the objective role set —
      existing manager/analyzer objectives keep their seq position and depends_on edges.
    """
    known_roles = {r["id"] for r in digest["roles"]}
    known_skills = {s["id"] for s in digest["skills"]}
    manager = next((r["id"] for r in digest["roles"] if r["kind"] == "manager"), "")
    analyzer = next((r["id"] for r in digest["roles"] if r["kind"] == "analyzer"), "")

    # Preserve objective order; one node per objective (duplicate role_ids allowed).
    steps: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for o in objectives:
        rid = str(o.get("role_id") or "").strip()
        if not rid or rid not in known_roles:
            continue
        oid = str(o.get("id") or "").strip() or f"seq-{o.get('seq')}-{len(steps)}"
        seq = int(o.get("seq") or 0) or (len(steps) + 1)
        key = f"role_{seq}_{rid}"
        # Collision-safe if two rows somehow share seq+role
        if any(s["key"] == key for s in steps):
            key = f"role_{seq}_{rid}_{len(steps)}"
        step = {
            "id": oid,
            "key": key,
            "seq": seq,
            "role_id": rid,
            "title": str(o.get("title") or "").strip(),
            "description": str(o.get("description") or "").strip(),
            "acceptance": str(o.get("acceptance") or "").strip(),
            "phase": str(o.get("phase") or "").strip().lower(),
            "skill_ids": [
                sid
                for sid in (str(s).strip() for s in (o.get("skill_ids") or []))
                if sid and sid in known_skills
            ],
            "depends_on": [str(d).strip() for d in (o.get("depends_on") or []) if str(d).strip()],
        }
        # de-dupe skill ids while preserving order
        step["skill_ids"] = list(dict.fromkeys(step["skill_ids"]))
        steps.append(step)
        by_id[oid] = step

    if not steps:
        return None

    present_roles = {s["role_id"] for s in steps}
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    edge_seen: set[tuple[str, str]] = set()

    def add_edge(frm: str, to: str, kind: str) -> None:
        if frm == to or (frm, to) in edge_seen:
            return
        edge_seen.add((frm, to))
        edges.append({"from": frm, "to": to, "kind": kind})

    for step in steps:
        rid = step["role_id"]
        title_bit = step["title"]
        desc = step["description"]
        phase = step["phase"]
        instr_parts: list[str] = []
        if title_bit and desc:
            instr_parts.append(f"{title_bit}: {desc}"[:800])
        elif title_bit or desc:
            instr_parts.append((title_bit or desc)[:800])
        if phase:
            instr_parts.append(f"Phase: {phase}.")
        if not instr_parts:
            instr_parts.append(
                "Coordinate the engagement."
                if rid == manager
                else "Produce the engagement report."
                if rid == analyzer
                else f"Carry out the {rid} work for this engagement."
            )
        node: dict[str, Any] = {
            "key": step["key"],
            "kind": "role",
            "ref_id": rid,
            "title": title_bit or None,
            "instruction": "\n\n".join(instr_parts),
            "acceptance": step["acceptance"][:600] or "Step complete and evidence recorded.",
        }
        if phase:
            node["phase"] = phase
        # Drop None title so _normalise does not store empty
        if not node.get("title"):
            node.pop("title", None)
        nodes.append(node)
        for i, sid in enumerate(step["skill_ids"]):
            sk = f"skill_{step['seq']}_{rid}_{sid}"
            if any(n["key"] == sk for n in nodes):
                sk = f"{sk}_{i}"
            nodes.append({"key": sk, "kind": "skill", "ref_id": sid})
            add_edge(step["key"], sk, "sequence" if i == 0 else "parallel")

    # depends_on → sequence edges (only among objectives we kept)
    dep_sets: dict[str, frozenset[str]] = {}
    for step in steps:
        dep_keys: list[str] = []
        for dep_id in step["depends_on"]:
            upstream = by_id.get(dep_id)
            if upstream is None:
                continue
            add_edge(upstream["key"], step["key"], "sequence")
            dep_keys.append(upstream["key"])
        dep_sets[step["key"]] = frozenset(dep_keys)

    # Siblings sharing the same dependency set: parallel fan-out from the earliest
    siblings: dict[frozenset[str], list[dict[str, Any]]] = {}
    for step in steps:
        siblings.setdefault(dep_sets[step["key"]], []).append(step)
    for group in siblings.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda s: (s["seq"], s["key"]))
        head = ordered[0]["key"]
        for peer in ordered[1:]:
            add_edge(head, peer["key"], "parallel")

    # Bookend manager / analyzer only when absent from the objective role set
    role_keys = [s["key"] for s in steps]
    if manager and manager not in present_roles:
        mkey = f"role_0_{manager}"
        nodes.insert(
            0,
            {
                "key": mkey,
                "kind": "role",
                "ref_id": manager,
                "instruction": "Coordinate the engagement.",
                "acceptance": "Specialists are briefed and the engagement is underway.",
                "phase": "recon",
            },
        )
        roots = [s["key"] for s in steps if not dep_sets.get(s["key"])]
        for rk in roots or role_keys[:1]:
            add_edge(mkey, rk, "sequence")

    if analyzer and analyzer not in present_roles:
        akey = f"role_999_{analyzer}"
        nodes.append(
            {
                "key": akey,
                "kind": "role",
                "ref_id": analyzer,
                "instruction": "Produce the engagement report.",
                "acceptance": "Report delivered with findings and evidence.",
                "phase": "reporting",
            }
        )
        # Leaves: role nodes with no outgoing sequence edge to another role
        has_role_successor = {
            e["from"]
            for e in edges
            if e["kind"] == "sequence" and e["from"] in role_keys and e["to"] in role_keys
        }
        leaves = [k for k in role_keys if k not in has_role_successor]
        for lk in leaves or role_keys[-1:]:
            add_edge(lk, akey, "sequence")

    target_type = seed_target_type if seed_target_type in TARGET_TYPES else "domain"
    seeds = [
        {
            "key": "target",
            "label": "Primary target",
            "kind": "scope",
            "required": True,
            "maps_to": "scope",
            "target_type": target_type,
        }
    ]
    return {
        "name": (title or "From project").strip()[:120],
        "description": (summary or f"Deduced from project “{title}”.").strip()[:600],
        "seed_inputs": seeds,
        "nodes": nodes,
        "edges": edges,
    }


def deduce_workflow_from_project(
    project_id: str, *, chat: Callable[[str, str], dict] | None = None
) -> dict[str, Any]:
    """Build a draft workflow from an existing project's plan (objectives + brief).

    Prefer a deterministic reconstruction from objective roles/skills; if the project
    has no usable roles, fall back to the LLM generator with a project-derived goal.
    """
    from peon.projects.db import ensure_project_database, using_project
    from peon.projects.models import Objective, Project, RulesOfEngagement

    pid = str(project_id or "").strip()
    if not pid:
        raise ValueError("project_id is required")
    project = Project.objects.filter(pk=pid).first()
    if project is None:
        raise ValueError(f"unknown project {pid!r}")

    ensure_project_database(pid)
    with using_project(pid):
        rows = list(
            Objective.objects.filter(project_id=pid)
            .prefetch_related("depends_on")
            .order_by("seq", "created_at")[:60]
        )
        objectives = [
            {
                "id": str(o.id),
                "seq": o.seq,
                "title": o.title,
                "description": o.description,
                "acceptance": o.acceptance_criteria,
                "phase": o.phase,
                "role_id": o.role_id,
                "skill_ids": list(o.skill_ids or []),
                "depends_on": [str(d.id) for d in o.depends_on.all()],
            }
            for o in rows
        ]
        roe = RulesOfEngagement.objects.filter(project_id=pid).first()
        seed_target_type = _seed_target_type_from_roe(list(roe.in_scope or []) if roe else [])

    digest = catalog_digest()
    draft = _draft_from_objectives(
        title=project.title,
        summary=project.summary or "",
        objectives=objectives,
        digest=digest,
        seed_target_type=seed_target_type,
    )
    if draft is not None:
        doc = _repair(_normalise(draft, digest), digest)
        return _finalize(doc, attempts=0)

    goal = _project_goal_text(
        title=project.title, summary=project.summary or "", objectives=objectives
    )
    return generate_workflow(goal, chat=chat)
