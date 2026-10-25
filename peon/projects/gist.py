"""Per-agent transcript → curated gist.

Each finished agent (Job) leaves a transcript in its project database. This
module compacts it, supplies it to Cognee as that agent's session document, and
asks the LLM to curate a *gist*: what the task was, what worked, what failed,
and concrete advice for planning a similar objective. Gists are stored as
memory entries (platform scope when they are target-agnostic, otherwise
project scope), so planners and agents recall lessons instead of raw logs.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from django.db import transaction

from peon.projects.models import Job, JobStatus, Project, StreamMessage, StreamMessageType

logger = logging.getLogger(__name__)

# Characters of transcript handed to the curator. This bounds the *LLM input*;
# the middle of an over-long run is replaced by an explicit omission marker.
TRANSCRIPT_BUDGET = 28_000
_LINE_CAP = 260
_MIN_TOOL_CALLS = 3

# Lifecycle chatter that says nothing about how the work went.
_NOISE = (
    "Running roles:",
    "sandbox docker:",
    "verified CLIs:",
    "Agent runtime",
    "crewai role=",
    "provisioned:",
    "Woken:",
)

SYSTEM_PROMPT = (
    "You curate one agent's work log into a GIST that helps people plan "
    "similar work later. Be concrete and honest about failures, including "
    "tooling or environment problems. Generalise: NEVER include target names, "
    "company names, domains, hostnames, IPs, URLs, emails or other identifiers "
    "from the log — describe them by kind ('a corporate domain', 'a /24'). "
    "Return JSON only, with keys: "
    '"outcome" ("success"|"partial"|"failed"), '
    '"task_kind" (short generic label), '
    '"approach" (2-4 sentences), '
    '"worked" (list of short strings), '
    '"failed" (list of short strings), '
    '"plan_advice" (list of concrete recommendations for planning a similar '
    "objective: skills/tools to use, how to split the work, checks to include), "
    '"avoid" (list of pitfalls). Use empty lists when there is nothing to say.'
)


def _one_line(text: str, cap: int = _LINE_CAP) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= cap else flat[: cap - 1] + "…"


def _own_rows(alias: str, job: Job) -> list[Any]:
    return list(
        StreamMessage.objects.using(alias)
        .filter(job_id=job.pk)
        .exclude(metadata__has_key="mirrored")
        .order_by("id")
    )


def render_transcript(job: Job, rows: list[Any], *, budget: int = TRANSCRIPT_BUDGET) -> str:
    """Compact, readable transcript: commands, key output, errors, outcome."""
    obj = getattr(job, "objective", None)
    lines = [
        f"AGENT: {job.title}",
        f"ROLE: {', '.join(job.role_ids or []) or '-'}  SKILLS: {', '.join(job.skill_ids or []) or '-'}",
        f"STATUS: {job.status}" + (f"  ERROR: {_one_line(job.error)}" if job.error else ""),
    ]
    if obj is not None:
        lines.append(f"OBJECTIVE: {_one_line(obj.title)} — accept: {_one_line(obj.acceptance_criteria or '')}")
    if job.description:
        lines.append(f"BRIEF: {_one_line(job.description, 600)}")
    lines.append("--- LOG ---")
    body: list[str] = []
    for row in rows:
        kind = str(row.message_type or "")
        text = str(row.content or "")
        if kind == StreamMessageType.TOOL:
            body.append(f"$ {_one_line(text)}")
        elif kind in {StreamMessageType.STDOUT, "stdout"}:
            body.append(f"  out: {_one_line(text)}")
        elif kind in {StreamMessageType.STDERR, "stderr"}:
            meta = row.metadata if isinstance(row.metadata, dict) else {}
            tag = "err" if meta.get("exit_code") != 0 else "note"
            body.append(f"  {tag}: {_one_line(text)}")
        elif kind == StreamMessageType.ERROR:
            body.append(f"ERROR: {_one_line(text, 400)}")
        elif kind == StreamMessageType.RESULT:
            body.append(f"RESULT: {_one_line(text, 1500)}")
        elif text and not text.startswith(_NOISE):
            body.append(f"- {_one_line(text, 200)}")
    deduped: list[str] = []
    for line in body:
        if not deduped or deduped[-1] != line:
            deduped.append(line)
    text = "\n".join(deduped)
    if len(text) > budget:
        head = int(budget * 0.35)
        tail = budget - head
        omitted = len(text) - budget
        text = f"{text[:head]}\n[… {omitted} chars of the middle omitted …]\n{text[-tail:]}"
    return "\n".join(lines) + "\n" + text


_ORCHESTRATION = ("Delegation:", "Operator input needed")
_AGENT_MESSAGE = re.compile(r"\[(request|handoff|inform)\]")


def _substantive(row: Any) -> bool:
    """A tool call, or an orchestration step (spawn, handoff, operator ask)."""
    if row.message_type == StreamMessageType.TOOL:
        return True
    text = str(row.content or "")
    return text.startswith(_ORCHESTRATION) or bool(_AGENT_MESSAGE.search(text[:120]))


def worth_curating(job: Job, rows: list[Any]) -> bool:
    """Skip trivial runs: a lesson needs actual work or a failure to learn from.

    A supervisor that only spawns workers and exchanges handoffs has few tool
    calls of its own but is exactly where planning lessons come from.
    """
    work = sum(1 for r in rows if _substantive(r))
    return work >= _MIN_TOOL_CALLS or job.status == JobStatus.FAILED or bool(job.error)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "task"


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [" ".join(str(v).split()) for v in value if str(v).strip()]
    return []


def render_gist(data: dict[str, Any]) -> tuple[str, str]:
    """``(description, body)`` for a memory entry from the curator's JSON."""
    outcome = str(data.get("outcome") or "partial").lower()
    kind = " ".join(str(data.get("task_kind") or "task").split())
    advice = _as_list(data.get("plan_advice"))
    desc = f"{kind}: {outcome}" + (f" — {advice[0]}" if advice else "")
    parts = [f"**Task:** {kind} (outcome: {outcome})", ""]
    if data.get("approach"):
        parts += [f"**Approach:** {' '.join(str(data['approach']).split())}", ""]
    for label, key in (
        ("Worked", "worked"),
        ("Failed / went wrong", "failed"),
        ("Plan advice", "plan_advice"),
        ("Avoid", "avoid"),
    ):
        items = _as_list(data.get(key))
        if items:
            parts.append(f"**{label}:**")
            parts += [f"- {i}" for i in items]
            parts.append("")
    return desc, "\n".join(parts).strip()


def curate_gist(job: Job, transcript: str) -> dict[str, Any] | None:
    """LLM-curated gist for one agent, or None when the LLM is unavailable."""
    from orchestrator.utils.llm import chat_json, llm_configured

    if not llm_configured():
        return None
    try:
        from orchestrator.utils import llm_usage

        with llm_usage.purpose("dream_gist_curation"):
            data = chat_json(SYSTEM_PROMPT, transcript)
    except Exception as exc:  # noqa: BLE001
        logger.warning("gist curation failed for job %s: %s", job.pk, exc)
        return None
    return data if isinstance(data, dict) else None


def store_gist(project: Project, job: Job, data: dict[str, Any]) -> str:
    """Persist as a platform lesson; fall back to project scope if identifiers leak."""
    from peon.projects import memory_store

    desc, body = render_gist(data)
    name = f"gist-{_slug(str(data.get('task_kind') or ''))}-{str(job.pk).replace('-', '')[:8]}"
    role = (job.role_ids or [""])[0]
    try:
        memory_store.save(
            None, name=name, kind="lesson", description=desc, body=body,
            role_id=role, created_by="dream",
        )
        return "platform"
    except ValueError as exc:
        logger.info("gist %s kept project-scoped: %s", name, exc)
    memory_store.save(
        project, name=name, kind="lesson", description=desc, body=body,
        role_id=role, job=job, created_by="dream",
    )
    return "project"


def supply_transcript(project: Project, job: Job, transcript: str) -> bool:
    """Give Cognee this agent's transcript as its own session document."""
    from orchestrator.crew.cognee_backend import CogneeBackend
    from orchestrator.crew.memory import job_session_id, memory_enabled, project_dataset

    if not memory_enabled():
        return False
    doc = {
        "kind": "transcript",
        "job_id": str(job.pk),
        "title": job.title,
        "role_id": (job.role_ids or [""])[0],
        "status": job.status,
        "project_id": str(project.pk),
        "summary": transcript,
        "indications": ["transcript", "agent"],
    }
    result = CogneeBackend.shared().remember(
        doc,
        dataset_name=project_dataset(str(project.pk)),
        session_id=job_session_id(job),
        self_improvement=False,
        run_in_background=True,
    )
    return bool(result.get("ok"))


def process_job(project: Project, alias: str, job: Job) -> dict[str, Any]:
    """Supply the transcript and curate the gist for one finished agent."""
    rows = _own_rows(alias, job)
    out: dict[str, Any] = {"job_id": str(job.pk), "title": job.title}
    if not worth_curating(job, rows):
        return {**out, "skipped": "trivial"}
    transcript = render_transcript(job, rows)
    out["supplied"] = supply_transcript(project, job, transcript)
    data = curate_gist(job, transcript)
    if data is None:
        return {**out, "gist": False, "retry": True}
    out["scope"] = store_gist(project, job, data)
    out["gist"] = True
    return out


# ── cross-run synthesis ──────────────────────────────────────────────────

SYNTH_MIN_NEW = 5
SYNTH_INPUT_BUDGET = 40_000
_SYNTH_PER_GIST = 1_400

SYNTH_SYSTEM = (
    "You consolidate many per-agent gists into overall lessons learned. Merge "
    "duplicates, favour patterns that recur across runs, and drop one-offs. "
    "Keep everything generic: NEVER include target, company or domain names, "
    "IPs, URLs or emails. Separate two things: (1) how to PLAN and RUN a kind of "
    "task well, and (2) defects or gaps in the platform/tooling that agents ran "
    "into (missing output, wrong defaults, silent limits, unavailable "
    "dependencies). Return JSON only: "
    '{"playbooks":[{"task_kind":"short generic label","when_to_use":"one line",'
    '"recommended_plan":["ordered, concrete steps/splits/checks"],'
    '"pitfalls":["..."],"tooling_notes":["..."],"evidence":<number of gists>}],'
    '"platform_improvements":[{"issue":"...","seen_in":<number>,'
    '"suggested_fix":"..."}]}. At most 8 playbooks.'
)


def _platform_gists() -> list[Any]:
    from peon.projects import memory_store

    entries = [e for e in memory_store._scope_qs(None) if e.name.startswith("gist-")]
    entries.sort(key=lambda e: e.updated_at, reverse=True)
    return entries


def synthesize_playbooks() -> dict[str, Any]:
    """Consolidate platform gists into playbooks + a platform-improvements note."""
    from orchestrator.utils.llm import chat_json, llm_configured
    from peon.projects import memory_store

    gists = _platform_gists()
    if not gists or not llm_configured():
        return {"ok": False, "reason": "no gists" if not gists else "llm not configured"}
    chunks: list[str] = []
    used = 0
    for e in gists:
        block = f"### {e.name}\n{(e.body or e.description).strip()[:_SYNTH_PER_GIST]}"
        if used + len(block) > SYNTH_INPUT_BUDGET:
            break
        chunks.append(block)
        used += len(block)
    try:
        from orchestrator.utils import llm_usage

        with llm_usage.purpose("dream_synthesis"):
            data = chat_json(SYNTH_SYSTEM, "\n\n".join(chunks))
    except Exception as exc:  # noqa: BLE001
        logger.warning("playbook synthesis failed: %s", exc)
        return {"ok": False, "reason": str(exc)[:200]}
    saved: list[str] = []
    for pb in (data.get("playbooks") or [])[:8] if isinstance(data, dict) else []:
        if not isinstance(pb, dict):
            continue
        kind = " ".join(str(pb.get("task_kind") or "").split())
        if not kind:
            continue
        body_parts = [f"**When to use:** {' '.join(str(pb.get('when_to_use') or '').split())}", ""]
        for label, key in (
            ("Recommended plan", "recommended_plan"),
            ("Pitfalls", "pitfalls"),
            ("Tooling notes", "tooling_notes"),
        ):
            items = _as_list(pb.get(key))
            if items:
                body_parts.append(f"**{label}:**")
                body_parts += [f"- {i}" for i in items]
                body_parts.append("")
        body_parts.append(f"_Consolidated from {int(pb.get('evidence') or 0)} run gist(s)._")
        try:
            memory_store.save(
                None,
                name=f"playbook-{_slug(kind)}",
                kind="lesson",
                description=f"{kind}: {' '.join(str(pb.get('when_to_use') or '').split())}"[:240],
                body="\n".join(body_parts).strip(),
                created_by="dream",
            )
            saved.append(f"playbook-{_slug(kind)}")
        except ValueError as exc:
            logger.info("playbook %r skipped: %s", kind, exc)
    improvements = _as_improvements(data.get("platform_improvements") if isinstance(data, dict) else None)
    if improvements:
        try:
            memory_store.save(
                None,
                name="platform-improvements",
                kind="reference",
                description="Recurring platform/tool issues seen in agent runs, with suggested fixes",
                body=improvements,
                created_by="dream",
            )
            saved.append("platform-improvements")
        except ValueError as exc:
            logger.info("platform-improvements skipped: %s", exc)
    return {"ok": True, "gists": len(gists), "saved": saved}


def _as_improvements(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    lines: list[str] = []
    for item in value:
        if not isinstance(item, dict) or not str(item.get("issue") or "").strip():
            continue
        issue = " ".join(str(item["issue"]).split())
        fix = " ".join(str(item.get("suggested_fix") or "").split())
        seen = int(item.get("seen_in") or 0)
        lines.append(f"- **{issue}** (seen in {seen} run(s))" + (f"\n  Fix: {fix}" if fix else ""))
    return "\n".join(lines)


def maybe_synthesize(cursor: dict[str, Any], *, now: str) -> dict[str, Any] | None:
    """Synthesize when enough new gists have accumulated since the last pass."""
    total = len(_platform_gists())
    last = cursor.get("synthesis") if isinstance(cursor.get("synthesis"), dict) else {}
    if total - int(last.get("gist_count") or 0) < SYNTH_MIN_NEW:
        return None
    result = synthesize_playbooks()
    if result.get("ok"):
        cursor["synthesis"] = {"at": now, "gist_count": total, "saved": result.get("saved", [])}
    return result


# ── catalog proposals (Dream suggests role/skill changes for review) ───────

PROPOSE_SYSTEM = (
    "You are a catalog curator. You are given (1) playbooks distilled from many "
    "agent runs, (2) a note on recurring platform/tooling issues, and (3) the "
    "current catalog of engagement roles and skills. Suggest concrete changes "
    "to the roles/skills catalog that would make future runs of the same kind "
    "of work better: a new role when a recurring task_kind has no good fit "
    "among the existing roles, an edit to an existing role's goal/backstory/"
    "aliases when a playbook implies it should approach its job differently, "
    "a new skill (as a full SKILL.md) when plan_advice/tooling_notes describe "
    "reusable step-by-step instructions no existing skill covers, or an edit to "
    "an existing skill's SKILL.md to fix a described gap. "
    "NEVER propose changing a role's `kind`, and never propose editing a role "
    "marked protected except to adjust its goal/backstory text. Never invent "
    "target/company/domain names. Propose at most 4 changes; propose none if "
    "nothing in the input clearly calls for one. For action=update, the role's "
    "`role_id` (or the skill_md frontmatter `name:`) must equal target_id — "
    "you are editing that existing one, not creating a new one. "
    "A SKILL.md starts with YAML frontmatter (--- / name / description / --- ) "
    "followed by markdown instructions. "
    "Return JSON only: {\"proposals\":[{"
    '"kind":"role"|"skill","action":"create"|"update",'
    '"target_id":"existing role_id/skill_id, or \\"\\" for create",'
    '"title":"short label for a reviewer","rationale":"1-3 sentences: which '
    'playbook/issue this addresses and why",'
    '"role":{"role_id":"...","kind":"engagement-pm|analyzer|technical",'
    '"aliases":["..."],"goal":"...","backstory":"..."} (role proposals only),'
    '"skill_md":"full SKILL.md text" (skill proposals only)'
    "}]}"
)
PROPOSE_INPUT_BUDGET = 20_000
PROPOSE_MAX_PENDING = 12


def _catalog_snapshot() -> str:
    from peon.projects.platform_catalog import PlatformCatalog

    catalog = PlatformCatalog.shared()
    lines = ["ROLES:"]
    for role in catalog.list_roles():
        goal = " ".join((role.goal or "").split())[:160]
        lines.append(
            f"- {role.id} (kind={role.kind}, protected={role.is_protected}): {goal}"
        )
    lines.append("")
    lines.append("SKILLS:")
    for skill in catalog.list_skills():
        desc = " ".join((skill.description or "").split())[:160]
        lines.append(f"- {skill.skill_id}: {desc}")
    return "\n".join(lines)


def _recent_lessons() -> str:
    from peon.projects import memory_store

    entries = [
        e
        for e in memory_store._scope_qs(None)
        if e.name.startswith("playbook-") or e.name == "platform-improvements"
    ]
    entries.sort(key=lambda e: e.updated_at, reverse=True)
    chunks: list[str] = []
    used = 0
    for e in entries:
        block = f"### {e.name}\n{(e.body or e.description).strip()}"
        if used + len(block) > PROPOSE_INPUT_BUDGET:
            break
        chunks.append(block)
        used += len(block)
    return "\n\n".join(chunks)


def _existing_pending(kind: str, action: str, target_id: str) -> bool:
    from peon.projects.models import CatalogProposal

    return CatalogProposal.objects.filter(
        status="pending", kind=kind, action=action, target_id=target_id
    ).exists()


def propose_catalog_changes() -> dict[str, Any]:
    """Ask the LLM for role/skill catalog changes and stage them for review.

    Nothing here ever touches the live catalog — each suggestion becomes a
    pending ``CatalogProposal`` row; a human approves or rejects it from the
    Catalog page.
    """
    from orchestrator.utils.llm import chat_json, llm_configured
    from peon.projects.models import CatalogProposal

    if not llm_configured():
        return {"ok": False, "reason": "llm not configured"}
    pending_count = CatalogProposal.objects.filter(status="pending").count()
    if pending_count >= PROPOSE_MAX_PENDING:
        return {"ok": False, "reason": "pending queue full", "pending": pending_count}
    lessons = _recent_lessons()
    if not lessons.strip():
        return {"ok": False, "reason": "no playbooks or platform-improvements yet"}
    user_input = f"{lessons}\n\n---\n\nCURRENT CATALOG\n{_catalog_snapshot()}"
    try:
        from orchestrator.utils import llm_usage

        with llm_usage.purpose("dream_catalog_proposals"):
            data = chat_json(PROPOSE_SYSTEM, user_input)
    except Exception as exc:  # noqa: BLE001
        logger.warning("catalog proposal generation failed: %s", exc)
        return {"ok": False, "reason": str(exc)[:200]}
    raw = data.get("proposals") if isinstance(data, dict) else None
    created: list[str] = []
    for item in (raw or [])[:4]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        action = str(item.get("action") or "").strip().lower()
        target_id = str(item.get("target_id") or "").strip().lower()
        if kind not in {"role", "skill"} or action not in {"create", "update"}:
            continue
        if action == "update" and not target_id:
            continue
        if action == "create" and target_id:
            continue
        if target_id and _existing_pending(kind, action, target_id):
            continue
        if kind == "role":
            role = item.get("role") if isinstance(item.get("role"), dict) else {}
            if not str(role.get("goal") or "").strip():
                continue
            payload: dict[str, Any] = {"role": role}
        else:
            skill_md = str(item.get("skill_md") or "").strip()
            if not skill_md:
                continue
            payload = {"skill_md": skill_md}
        row = CatalogProposal.objects.create(
            kind=kind,
            action=action,
            target_id=target_id,
            title=" ".join(str(item.get("title") or "").split())[:200]
            or f"{action} {kind} {target_id or '(new)'}",
            rationale=" ".join(str(item.get("rationale") or "").split()),
            payload=payload,
        )
        created.append(str(row.pk))
    return {"ok": True, "created": created}


def apply_proposal(proposal_id: str, *, decision: str) -> dict[str, Any]:
    """Approve (writes the live catalog) or reject a pending proposal."""
    import uuid as uuid_mod

    from peon.projects.models import CatalogProposal
    from peon.projects.platform_catalog import PlatformCatalog

    if decision not in {"approve", "reject"}:
        return {"ok": False, "error": "decision must be approve or reject"}
    try:
        uuid_mod.UUID(str(proposal_id))
    except (ValueError, TypeError):
        return {"ok": False, "error": "invalid proposal id"}
    row = CatalogProposal.objects.filter(pk=proposal_id, status="pending").first()
    if row is None:
        return {"ok": False, "error": "proposal not found or already decided"}
    if decision == "reject":
        row.status = "rejected"
        row.decided_at = _now_dt()
        row.save(update_fields=["status", "decided_at"])
        return {"ok": True, "status": "rejected"}
    catalog = PlatformCatalog.shared()
    try:
        if row.kind == "role":
            role = row.payload.get("role") or {}
            catalog.upsert_role(
                role_id=str(role.get("role_id") or row.target_id or ""),
                kind=str(role.get("kind") or ""),
                aliases=role.get("aliases") or [],
                previous_id=row.target_id,
                goal=str(role.get("goal") or ""),
                backstory=str(role.get("backstory") or ""),
            )
        elif row.kind == "skill":
            catalog.upsert_skill(
                skill_md=str(row.payload.get("skill_md") or ""),
                skill_id=row.target_id,
            )
        elif row.kind == "mcp_role_skill":
            payload = row.payload or {}
            role = payload.get("role") or {}
            mcp_id = str(payload.get("mcp_id") or row.target_id or "")
            skill_id = str(payload.get("skill_id") or mcp_id)
            skill_md = str(payload.get("skill_md") or "")
            if not (mcp_id and role.get("role_id") and skill_md):
                raise ValueError("mcp_role_skill proposal is incomplete")
            with transaction.atomic():
                saved_role = catalog.upsert_role(
                    role_id=str(role.get("role_id")),
                    kind=str(role.get("kind") or "technical"),
                    aliases=role.get("aliases") or [],
                    goal=str(role.get("goal") or ""),
                    backstory=str(role.get("backstory") or ""),
                )
                catalog.upsert_skill(skill_md=skill_md, skill_id=skill_id, bindings=[])
                catalog.replace_bindings(
                    owner_kind="skill",
                    owner_id=skill_id,
                    bindings=[{"role_id": saved_role.id, "score": 1.0, "lead": True}],
                )
                catalog.replace_bindings(
                    owner_kind="mcp",
                    owner_id=mcp_id,
                    bindings=[{"role_id": saved_role.id, "score": 1.0, "lead": True}],
                )
        elif row.kind == "mcp_binding":
            payload = row.payload or {}
            owner_id = str(payload.get("owner_id") or row.target_id or "")
            if not owner_id:
                raise ValueError("mcp_binding proposal has no owner_id")
            candidates = payload.get("candidates") or []
            catalog.replace_bindings(
                owner_kind=str(payload.get("owner_kind") or "mcp"),
                owner_id=owner_id,
                bindings=[
                    {
                        "role_id": c.get("role_id"),
                        "score": c.get("score", 1.0),
                        "lead": bool(c.get("lead")),
                    }
                    for c in candidates
                    if isinstance(c, dict) and c.get("role_id")
                ],
            )
        else:
            raise ValueError(f"unknown proposal kind {row.kind!r}")
    except Exception as exc:  # noqa: BLE001
        row.error = str(exc)[:1000]
        row.save(update_fields=["error"])
        return {"ok": False, "error": str(exc)}
    row.status = "approved"
    row.decided_at = _now_dt()
    row.error = ""
    row.save(update_fields=["status", "decided_at", "error"])
    return {"ok": True, "status": "approved"}


def _now_dt():
    from django.utils import timezone as dj_tz

    return dj_tz.now()
