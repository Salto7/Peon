"""CrewAI tools. Command tools call ``SandboxTools``; engagement tools use the job bridge."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from orchestrator.agent.delegation import (
    list_delegations_body,
    list_roles_body,
    list_work_body,
    propose_work_body,
    spawn_work_body,
    wait_for_work_body,
)
from orchestrator.agent.job import get_job
from orchestrator.crew.tools.catalog import crew_tool
from orchestrator.skills.runtime.bodies import (
    load_skill_body,
    read_skill_resource_body,
    run_skill_script_body,
)
from orchestrator.skills.runtime.sandbox import SandboxTools


def _bridge(method: str) -> Callable[..., str]:
    def call(*args: Any, **kwargs: Any) -> str:
        return getattr(get_job().bridge, method)(*args, **kwargs)

    return call


@crew_tool("sandbox_status", "Show bound sandbox mode and name.")
def sandbox_status() -> str:
    return SandboxTools().status()


@crew_tool(
    "provision_cli",
    "Install/verify a CLI on PATH via tools/catalog cascade. Prefer over apt/curl.",
)
def provision_cli(binary: str, package: str = "") -> str:
    return SandboxTools().provision(
        binary, package=package, enforce_role_allowlist=True
    )


@crew_tool(
    "bash",
    "Claude Bash tool. Run a shell command in this job's project sandbox "
    "(RoE applies). Nothing runs on the host.",
    tags=("shell",),
)
def bash(command: str) -> str:
    return SandboxTools().bash(command)


def _lats_tree_path():
    from peon.projects.workspaces import project_workspace_dir

    job = get_job()
    root = project_workspace_dir(job.project_id) / ".peon" / "lats"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{job.job_id}.json"


def _lats_finding_count() -> int:
    from peon.projects.db import ensure_project_database, using_project
    from peon.projects.models import Finding

    job = get_job()
    if not job.project_id:
        return 0
    alias = ensure_project_database(job.project_id)
    with using_project(job.project_id):
        return Finding.objects.using(alias).filter(job_id=job.job_id).count()


@crew_tool(
    "explore_branches",
    "LATS: propose 2-5 candidate shell-command probes for the SAME decision "
    "point (different params/payloads/techniques toward the same lead) when "
    "unsure which is most likely to pay off. Runs each, scores it, and tells "
    "you which branch to deepen next — systematic exploration instead of "
    "trying one thing, backtracking, trying another by feel. Builds on this "
    "job's own tree across repeated calls (budget: 24 rollouts / 60 nodes). "
    "candidates_json: JSON array of {command, rationale} (plain sandbox shell "
    "commands — same as bash). Omit to just see the current frontier/report.",
)
def explore_branches(candidates_json: str = "") -> str:
    from orchestrator.exploit.lats import (
        MAX_DEPTH,
        LatsTree,
        backprop,
        probe_value,
        select_frontier,
    )

    path = _lats_tree_path()
    if path.is_file():
        try:
            tree = LatsTree.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, KeyError, ValueError):
            tree = LatsTree.new(get_job().brief)
    else:
        tree = LatsTree.new(get_job().brief)

    candidates: list[dict[str, str]] = []
    if candidates_json.strip():
        try:
            raw = json.loads(candidates_json)
        except json.JSONDecodeError as exc:
            return f"Error: candidates_json is not valid JSON: {exc}"
        if not isinstance(raw, list):
            return "Error: candidates_json must be a JSON array."
        for item in raw[:5]:
            if not isinstance(item, dict) or not str(item.get("command") or "").strip():
                continue
            candidates.append(
                {
                    "command": str(item["command"]).strip(),
                    "rationale": str(item.get("rationale") or "").strip(),
                }
            )
        if not candidates:
            return "Error: no usable {command, rationale} entries in candidates_json."

    lines: list[str] = []
    if candidates:
        if tree.budget_hit():
            return (
                f"LATS budget exhausted ({tree.rollouts} rollouts, {len(tree.nodes)} nodes) "
                "for this job — fall back to normal reasoning rather than growing this tree further."
            )
        frontier_id = select_frontier(tree)
        frontier = tree.nodes[frontier_id]
        if frontier.depth >= MAX_DEPTH:
            lines.append(
                f"Frontier node is at max depth ({MAX_DEPTH}); candidates attached to it "
                "but will not expand further after this."
            )
        tools = SandboxTools()
        from agent_runtime.api import Session
        from orchestrator.sandbox.tool_output import render_tool_output

        for cand in candidates:
            node = tree.add_child(frontier_id, command=cand["command"], rationale=cand["rationale"])
            before = _lats_finding_count()
            cmd, result = tools.bash_result(node.command)
            after = _lats_finding_count()
            if cmd is None:
                output = result.stderr or "Error: command refused"
                exit_code = result.code
            else:
                output = render_tool_output(Session.current(), result, label="bash")
                exit_code = result.code
            node.output = output
            node.exit_code = exit_code
            node.status = "evaluated"
            value = probe_value(
                exit_code=exit_code, output=output, found_new_finding=after > before
            )
            node.value = value
            backprop(tree, node.id, value)
            tree.rollouts += 1
            lines.append(
                f"- [{value:.2f}] {node.command!r} ({node.rationale or 'no rationale'})\n"
                f"  {output.strip()[:300]!r}"
            )

    next_id = select_frontier(tree)
    next_node = tree.nodes[next_id]
    path.write_text(json.dumps(tree.to_dict()), encoding="utf-8")

    report = "\n".join(lines) if lines else "(no new probes run this call)"
    if next_node.status == "evaluated" and not next_node.children:
        recommend = (
            f"Best branch to deepen next: {next_node.command!r} (value={next_node.value:.2f}) "
            f"at depth {next_node.depth} — propose 2-5 follow-up probes that build on it."
        )
    elif next_node.id == tree.root_id and not tree.nodes[tree.root_id].children:
        recommend = "Tree is empty — propose 2-5 initial probes for this decision point."
    else:
        recommend = "Nothing further to expand under the current frontier; tree may be exhausted."
    return (
        f"{report}\n\n"
        f"Tree: {len(tree.nodes)} nodes, {tree.rollouts} rollouts. {recommend}"
    )


@crew_tool(
    "run_periodic",
    "Repeat a sandbox shell command on an interval (watchdog ticks). RoE applies.",
)
def run_periodic(
    command: str,
    interval_seconds: int = 30,
    duration_seconds: int = 120,
    package: str = "",
) -> str:
    return SandboxTools().periodic(
        command,
        interval_seconds=interval_seconds,
        duration_seconds=duration_seconds,
        package=package,
    )


@crew_tool("roe_status", "Show Rules of Engagement summary for this project.")
def roe_status() -> str:
    return _bridge("roe_summary")()


@crew_tool(
    "assert_in_scope",
    "Check whether a target value is authorized under active RoE before probing.",
)
def assert_in_scope(target: str) -> str:
    value = (target or "").strip()
    if not value:
        return "Error: empty target."
    reason = get_job().bridge.assert_in_scope(value)
    if reason:
        return f"DENIED: {reason}"
    return f"ALLOWED: {value} is in-scope under current RoE."


@crew_tool(
    "record_finding",
    "Record one engagement subject discovery with optional evidence provenance. "
    "Required: title plus at least one subject (host/port or subjects JSON). "
    "evidence_path is workspace provenance only — never a graph subject. "
    "Do not record objective/job/agent status — use update_objective_status.",
)
def record_finding(
    title: str,
    summary: str = "",
    severity: str = "info",
    kind: str = "",
    host: str = "",
    port: str = "",
    protocol: str = "",
    evidence_path: str = "",
    subjects_json: str = "",
) -> str:
    fields: dict = {
        "title": title,
        "description": summary,
        "summary": summary,
        "severity": severity,
        "kind": kind,
        "host": host,
        "protocol": protocol,
        "evidence_path": evidence_path,
    }
    if str(port).strip():
        fields["port"] = port
    raw_subjects = (subjects_json or "").strip()
    if raw_subjects:
        try:
            parsed = json.loads(raw_subjects)
        except json.JSONDecodeError as exc:
            return f"Error: subjects_json must be JSON list: {exc}"
        if not isinstance(parsed, list):
            return "Error: subjects_json must be a JSON list."
        fields["subjects"] = parsed
    return get_job().bridge.record_finding(**fields)


@crew_tool(
    "record_findings",
    "Record multiple subject discoveries from a JSON list. Each object needs "
    "title and at least one subject (host/port or subjects:[{type,value}]). "
    "evidence_path is provenance only. Use when one artifact yields many rows.",
)
def record_findings(findings_json: str) -> str:
    return get_job().bridge.record_findings(findings_json or "[]")


@crew_tool(
    "recall_memory",
    "Search persistent memory: notes from this engagement (operator feedback, "
    "facts, decisions) and generic platform lessons. Use a natural-language "
    "query about what you are about to do (a tool, a target type, a failure). "
    "Check it before repeating work that may already have been tried.",
)
def recall_memory(query: str) -> str:
    return get_job().bridge.recall_memory(query)


@crew_tool(
    "save_memory",
    "Save something durable you learned so later agents (and later runs) do not "
    "rediscover it. kind: lesson (method/tool behaviour that worked or failed) | "
    "feedback (operator preference/correction) | fact (durable fact about the "
    "target or environment) | decision (plan rationale) | reference (where "
    "something lives). description is a one-line hook shown in everyone's index; "
    "body should include **Why:** and **How to apply:**. Same name updates the "
    "entry. scope: project (default) or platform — platform is for generic "
    "lessons only and must not contain targets, IPs, emails, URLs or customer "
    "data. Do not save what is already in findings/RoE or is trivially re-derivable.",
)
def save_memory(
    name: str,
    kind: str,
    description: str,
    body: str = "",
    scope: str = "project",
) -> str:
    return get_job().bridge.save_memory(name, kind, description, body, scope or "project")


@crew_tool(
    "forget_memory",
    "Retire a memory that turned out wrong or stale (by name; scope project|platform).",
)
def forget_memory(name: str, scope: str = "project") -> str:
    return get_job().bridge.forget_memory(name, scope or "project")


@crew_tool(
    "list_credentials",
    "List credential names/kinds in this project's key store (never values).",
)
def list_credentials() -> str:
    return get_job().bridge.list_credentials()


@crew_tool(
    "get_credential",
    "Plaintext of a stored project credential, by name "
    "(part: secret | username | domain | full).",
)
def get_credential(name: str, part: str = "secret") -> str:
    return get_job().bridge.get_credential(name, part or "secret")


@crew_tool(
    "list_team",
    "List every agent on this project (id, objective OBJ-n, role, A2A state, what "
    "idle agents wait for). Use before send_message to address the right agent.",
)
def list_team() -> str:
    return get_job().bridge.list_team()


@crew_tool(
    "send_message",
    "Send an A2A message to one or more agents on this project, each addressed "
    "individually — this is never a broadcast. `to` is an agent id from list_team "
    "(full id or its first 8 characters); for more than one recipient, "
    "comma-separate ids (capped — list_team shows the limit). Role ids / "
    "objectives are not accepted as `to`. Put the work detail in `content`: what "
    "you found, what you need, relevant paths. If a recipient is idle it wakes "
    "and picks the work up; if it already finished, it resumes from its last "
    "checkpoint to read your message. `reply_to` is optional: pass a message_id "
    "from your own inbox to link this as a reply to it. kind: inform | request | "
    "reply. Prefer this over spawn_work when a suitable agent already exists. A "
    "per-pair message cap guards against ping-pong loops — if you hit it, stop "
    "messaging and finish your turn instead of retrying.",
)
def send_message(to: str, content: str, kind: str = "inform", reply_to: str = "") -> str:
    return get_job().bridge.send_message(
        to, content, kind=kind or "inform", reply_to=reply_to or ""
    )


@crew_tool(
    "check_inbox",
    "Read and consume messages other agents or the operator sent you. Call this "
    "between steps of long work and after waking.",
)
def check_inbox() -> str:
    return get_job().bridge.check_inbox()


@crew_tool(
    "message_status",
    "Check the delivery/read state of messages YOU sent (most recent first): "
    "whether each was read yet and whether the recipient replied. Use this "
    "instead of re-sending or polling when you are waiting on an answer; prefer "
    "go_idle if you have nothing else to do while you wait.",
)
def message_status(limit: int = 10) -> str:
    return get_job().bridge.message_status(limit=int(limit or 10))


@crew_tool(
    "go_idle",
    "Park yourself until another agent or the operator messages you. Use when "
    "you finished what you can do and need another agent's output to continue — "
    "do not poll. Then end your turn with a short summary of what is done and "
    "what you are waiting for; you resume from your context when woken.",
)
def go_idle(reason: str = "") -> str:
    return get_job().bridge.go_idle(reason or "")


@crew_tool(
    "record_assets",
    "Add discovered assets and typed relations to the engagement asset graph "
    "(attack surface). assets_json: [{\"type\":\"fqdn\",\"value\":\"a.example.com\"}, ...] "
    "or \"type:value\" strings. relations_json: [{\"from\":\"fqdn:a.example.com\","
    "\"rel\":\"a_record\",\"to\":\"ip:203.0.113.5\"}]. Types follow the Open Asset "
    "Model: fqdn, ip, netblock, asn, service, url, certificate, organization, person, "
    "email, phone, account, file, product, location. Relation labels: a_record, "
    "aaaa_record, cname_record, ns_record, mx_record, ptr_record, srv_record, node "
    "(subdomain), port, contains, announces, registration, managed_by, owns, "
    "associated_with — or any custom slug. Recording an asset does NOT authorize it "
    "(Rules of Engagement still decide scope). Use for infrastructure and "
    "relationships; use record_finding for security findings.",
)
def record_assets(assets_json: str = "[]", relations_json: str = "[]") -> str:
    return get_job().bridge.record_assets(assets_json or "[]", relations_json or "[]")


@crew_tool(
    "list_findings",
    "List recorded findings for this project (optional kind filter).",
)
def list_findings(kind: str = "") -> str:
    return get_job().bridge.list_findings(kind=kind)


@crew_tool(
    "search_evidence",
    "Search Evidence across this project — including what other jobs captured, "
    "not just your own. Filter by kind (command_output, screenshot, traffic, …), "
    "a Finding's host, or a Finding seq (FIND-n). Use before re-capturing "
    "something another agent may already have evidence for.",
)
def search_evidence(kind: str = "", host: str = "", finding_seq: int = 0) -> str:
    return get_job().bridge.search_evidence(
        kind=kind, host=host, finding_seq=int(finding_seq or 0)
    )


@crew_tool(
    "record_observation",
    "Note something about an asset-graph node (host/service/url — the same "
    "value you'd pass to record_assets) that isn't confirmed enough for "
    "record_finding yet, e.g. \"looks like a staging environment\". "
    "asset_key is the asset's value (or type:value).",
)
def record_observation(asset_key: str, text: str) -> str:
    return get_job().bridge.record_observation(asset_key, text)


@crew_tool(
    "record_intent",
    "Plan something about an asset-graph node: what you intend to do with "
    "it next, or why you're skipping it. status: planned (default) | "
    "in_progress | done | skipped.",
)
def record_intent(asset_key: str, text: str, status: str = "planned") -> str:
    return get_job().bridge.record_intent(asset_key, text, status=status or "planned")


@crew_tool(
    "list_workspace_artifacts",
    "List evidence files prior agents wrote under workspace/ (and findings/). "
    "Use before synthesizing a report — do not re-run scans to rediscover files.",
)
def list_workspace_artifacts() -> str:
    return _bridge("list_workspace_artifacts")()


@crew_tool(
    "read_workspace_artifact",
    "Read one evidence file by relative path (workspace/… or findings/…). "
    "Prefer this over shell cat for report synthesis.",
)
def read_workspace_artifact(path: str, max_chars: int = 100000) -> str:
    return _bridge("read_workspace_artifact")(path, max_chars=int(max_chars))


@crew_tool(
    "write_workspace_artifact",
    "Write a text deliverable (synthesis, manifest, JSON) to a relative path "
    "under workspace/. Does not execute anything; use it to persist your lane's "
    "output instead of spawning a job just to create a file.",
)
def write_workspace_artifact(path: str, content: str) -> str:
    return _bridge("write_workspace_artifact")(path, content)


@crew_tool(
    "write_report_note",
    "Append a markdown section to findings/report.md. Analyzer-only; "
    "does not create a Finding row.",
)
def write_report_note(section: str, body: str) -> str:
    return _bridge("write_report_note")(section, body)


@crew_tool(
    "list_objectives",
    "List persisted project objectives (seq, status, title, role).",
)
def list_objectives() -> str:
    return get_job().bridge.list_objectives()


@crew_tool(
    "update_objective_status",
    "Update an objective status (pending/in_progress/completed/blocked/cancelled). "
    "Use for scheduler progress — never record this as a finding.",
)
def update_objective_status(seq: int, status: str, note: str = "") -> str:
    return get_job().bridge.update_objective_status(int(seq), status, note)


# --- multi-agent / collaboration (auto-injected for engagement roles) ---------


@crew_tool(
    "list_roles",
    "Browse the live CrewAI role catalog (id, goal, capabilities). "
    "Use before spawn_work to pick an existing role_id (or omit role_id to "
    "spawn the same role as this job).",
)
def list_roles() -> str:
    return list_roles_body()


@crew_tool(
    "spawn_work",
    "Spawn a child Job (default) or peer Job for in-lane follow-on skills or when "
    "the operator/brief asks for parallel work. Empty role_id = same expert. "
    "Optional skill_ids (comma-separated subset of this role's skill group). "
    "link=child|peer. Optional scope_slice_json under RoE — do not invent smaller "
    "CIDR slices of an authorized netblock unless the operator asked. After "
    "spawning, if you have nothing else to do, go_idle('waiting on children') — "
    "each child's result wakes you; use wait_for_work only for a quick status "
    "peek, never as a wait loop. Do not replan via PM for work already in your "
    "skill group.",
)
def spawn_work(
    title: str,
    description: str,
    role_id: str = "",
    link: str = "child",
    scope_slice_json: str = "",
    skill_ids: str = "",
) -> str:
    return spawn_work_body(
        title,
        description,
        role_id=role_id,
        link=link,
        scope_slice_json=scope_slice_json,
        skill_ids=skill_ids,
    )


@crew_tool(
    "wait_for_work",
    "Non-blocking status of child jobs this agent spawned. After spawn_work, call "
    "again later until all children are terminal; do not busy-wait in a tight loop.",
)
def wait_for_work(timeout_seconds: int = 600, job_ids: str = "") -> str:
    return wait_for_work_body(
        timeout_seconds=int(timeout_seconds), job_ids=job_ids or ""
    )


@crew_tool(
    "list_work",
    "List other Jobs on the same objective (peers / siblings).",
)
def list_work() -> str:
    return list_work_body()


@crew_tool(
    "list_delegations",
    "Show the spawn/delegation tree for this project or objective "
    "(manager oversight).",
)
def list_delegations() -> str:
    return list_delegations_body()


@crew_tool(
    "propose_work",
    "LLM-propose focused child/peer Jobs from objective context + transcript; "
    "spawns only existing catalog role ids.",
)
def propose_work(context_notes: str = "", max_agents: int = 4) -> str:
    return propose_work_body(
        context_notes=context_notes or "", max_agents=int(max_agents or 4)
    )


@crew_tool(
    "ask_operator",
    "Ask the human operator a question (HITL popup). Use when RoE is ambiguous, "
    "replans are stuck, or a decision is required. options = comma-separated "
    "choices. blocks_job=true also blocks the current objective until reply.",
)
def ask_operator(
    question: str,
    options: str = "",
    blocks_job: bool = True,
) -> str:
    return get_job().bridge.ask_operator(
        question,
        options=options or "",
        blocks_job=bool(blocks_job),
    )


@crew_tool(
    "load_skill",
    "Load the full instructions for a progressive skill (claude/<name> or name).",
)
def load_skill(name: str) -> str:
    return load_skill_body(name)


@crew_tool(
    "read_skill_resource",
    "Read a file inside a skill pack. path is relative to the skill root.",
)
def read_skill_resource(name: str, path: str) -> str:
    return read_skill_resource_body(name, path)


@crew_tool(
    "run_skill_script",
    "Run a skill script in this job's project sandbox. "
    "args is a JSON list of strings. "
    "cwd is the writable workspace; $SKILL_DIR is the read-only pack root; "
    "artifacts belong in the project workspace.",
)
def run_skill_script(
    name: str,
    relative_path: str,
    args: str = "",
) -> str:
    return run_skill_script_body(name, relative_path, args)


@crew_tool(
    "list_mcp_tools",
    "List tools on an MCP server bound to this role. The server process runs "
    "inside the project sandbox. name is the catalog id.",
)
def list_mcp_tools(name: str) -> str:
    from orchestrator.mcp.provision import list_mcp_tools as _list

    return _list(name)


@crew_tool(
    "call_mcp_tool",
    "Call a tool on an MCP server inside the project sandbox. "
    "arguments_json is a JSON object. Large results spill to "
    "workspace/mcp/artifacts/ with a preview + artifact path. "
    "Endpoint values come from the project, not from this call.",
)
def call_mcp_tool(name: str, tool: str, arguments_json: str = "{}") -> str:
    from orchestrator.mcp.provision import call_mcp_tool as _call

    return _call(name, tool, arguments_json)


@crew_tool(
    "list_mcp_resources",
    "List MCP Resources exposed by a bound server (official MCP resources/list). "
    "name is the catalog id.",
)
def list_mcp_resources(name: str) -> str:
    from orchestrator.mcp.provision import list_mcp_resources as _list

    return _list(name)


@crew_tool(
    "read_mcp_resource",
    "Read one MCP Resource by URI (resources/read). Large bodies spill to "
    "workspace/mcp/artifacts/ like call_mcp_tool.",
)
def read_mcp_resource(name: str, uri: str) -> str:
    from orchestrator.mcp.provision import read_mcp_resource as _read

    return _read(name, uri)
