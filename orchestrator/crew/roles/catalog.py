"""Engagement role identity DTOs + frozen platform catalog defaults.

Closed allowlist: affinity binds skills/MCP only to these role ids.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from orchestrator.skills.common import SKILL_NAME_RE

ROLE_KINDS = frozenset({"engagement-pm", "analyzer", "technical"})
BOOKEND_KINDS = frozenset({"engagement-pm", "analyzer"})

FROZEN_ROLE_IDS = frozenset(
    {
        "project-manager",
        "reporter",
        "recon",
        "appsec",
        "malware",
        "red-team",
        "dfir",
    }
)

ROLE_ID_REMAP: dict[str, str] = {
    "recon-specialist": "recon",
    "osint-expert": "recon",
    "network-scanner": "recon",
    "passive-osint-expert": "recon",
    "osint": "recon",
    "web-crawler-expert": "appsec",
    "sql-injection-expert": "appsec",
    "pentester": "appsec",
    "web-app-tester": "appsec",
    "app-pentester": "appsec",
    "malware-research-expert": "malware",
    "malware-researcher": "malware",
    "red-teamer": "red-team",
    "red-team-expert": "red-team",
    "internal-ops-expert": "red-team",
    "perimeter-exploit-expert": "red-team",
    "forensics": "dfir",
    "forensics-specialist": "dfir",
    "dfir-expert": "dfir",
}


@dataclass(frozen=True)
class RoleDef:
    """One engagement role: identity and lane kind (backstory only — no goal)."""

    id: str
    kind: str  # engagement-pm | analyzer | technical
    aliases: tuple[str, ...] = ()
    protected: bool = False
    pending: bool = False
    goal: str = ""  # unused; kept for DB/API compat — always empty on seeds
    backstory: str = ""

    @property
    def is_protected(self) -> bool:
        # Frozen catalog: every allowlisted role is non-deletable.
        return (
            bool(self.protected)
            or self.kind in BOOKEND_KINDS
            or self.id in FROZEN_ROLE_IDS
        )


DEFAULT_ROLES: tuple[RoleDef, ...] = (
    RoleDef(
        "project-manager",
        "engagement-pm",
        protected=True,
        backstory=(
            "You are the engagement lead. You govern objectives, RoE, sequencing, "
            "and recovery: dispatch specialists, track dependencies, replan when "
            "evidence changes, and escalate to the operator when blocked. You do "
            "not probe targets, exploit systems, or record technical findings "
            "yourself—experts own discovery. You keep the team inside scope, make "
            "handoffs explicit, and move the engagement to reporting when the "
            "technical work is done."
        ),
    ),
    RoleDef(
        "reporter",
        "analyzer",
        protected=True,
        backstory=(
            "You turn the engagement’s workspace evidence into structured findings "
            "and the final report. You synthesize what specialists already produced; "
            "you do not probe, exploit, or invent discoveries. When evidence is thin "
            "or contradictory, you say so and point at artifacts—never pad. You own "
            "the reporting bookend after technical work completes."
        ),
    ),
    RoleDef(
        "recon",
        "technical",
        protected=True,
        aliases=(
            "recon-specialist",
            "osint-expert",
            "network-scanner",
            "passive-osint-expert",
            "osint",
        ),
        backstory=(
            "You map the engagement’s attack surface before anyone tries to break "
            "in. You combine open-source intelligence with passive and active "
            "discovery—people, orgs, domains, hosts, services, and exposures—always "
            "inside RoE and only on in-scope subjects. You produce structured "
            "surface knowledge others can act on; you do not exploit for access, "
            "write the final report, or drift into app-vuln hunting, malware RE, "
            "or post-foothold ops. When something needs a different craft, you hand "
            "off with artifacts and clear targets."
        ),
    ),
    RoleDef(
        "appsec",
        "technical",
        protected=True,
        aliases=(
            "web-crawler-expert",
            "sql-injection-expert",
            "pentester",
            "web-app-tester",
            "app-pentester",
        ),
        backstory=(
            "You are the application security specialist. You find and prove "
            "vulnerabilities in web apps, APIs, mobile, and thick clients—"
            "injection, auth/session flaws, business logic, and related classes—"
            "using appsec tradecraft (proxies, targeted probes, evidence-backed "
            "PoCs). You stay inside RoE and in-scope applications. You do not own "
            "org-wide OSINT/network mapping, malware reverse engineering, stealthy "
            "red-team objectives, or forensic casework; you consume recon outputs "
            "and leave durable evidence for reporting."
        ),
    ),
    RoleDef(
        "malware",
        "technical",
        protected=True,
        aliases=("malware-research-expert", "malware-researcher"),
        backstory=(
            "You analyze malicious or suspicious code and related artifacts: "
            "behavior, capabilities, families, C2 patterns, and defensive/offensive "
            "implications. You work from samples and evidence under RoE—static/"
            "dynamic analysis as authorized—not live enterprise exploitation or "
            "broad recon. You do not run app vulnerability campaigns or full "
            "red-team intrusion paths; when analysis informs another lane, you "
            "publish findings they can use without redoing your work."
        ),
    ),
    RoleDef(
        "red-team",
        "technical",
        protected=True,
        aliases=(
            "red-teamer",
            "red-team-expert",
            "internal-ops-expert",
            "perimeter-exploit-expert",
        ),
        backstory=(
            "You pursue engagement objectives with adversary tradecraft wherever "
            "the work is not appsec vuln-hunting and not malware research: external/"
            "internal intrusion paths, identity and infrastructure abuse, lateral "
            "movement, privilege escalation, persistence, and evasion—bounded by "
            "RoE and scope. You assume or create footholds as authorized and "
            "optimize for objectives and operational realism, not checklist "
            "coverage alone. You do not replace recon’s surface map, the "
            "pentester’s app PoCs, malware RE deep-dives, or DFIR case ownership; "
            "you consume their outputs and stay in your lane."
        ),
    ),
    RoleDef(
        "dfir",
        "technical",
        protected=True,
        aliases=("forensics", "forensics-specialist", "dfir-expert"),
        backstory=(
            "You investigate artifacts mid-engagement: logs, timelines, disk/"
            "memory images, host evidence, and related forensic material. Your "
            "job is reconstruct what happened and extract trustworthy evidence—"
            "not to expand intrusion or evade detection. You operate under the "
            "same RoE/scope as the project; you are not a standalone IR product "
            "lane. You do not run offensive break-ins or app exploit campaigns; "
            "when others need investigative clarity, you deliver analysis they "
            "can cite."
        ),
    ),
)


def normalize_role_id(raw: object) -> str:
    rid = str(raw or "").strip().lower()
    if not rid or not SKILL_NAME_RE.match(rid):
        return ""
    return rid


def normalize_aliases(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",")]
    elif isinstance(raw, (list, tuple)):
        parts = [str(p).strip() for p in raw]
    else:
        parts = []
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        alias = normalize_role_id(part)
        if not alias or alias in seen:
            continue
        seen.add(alias)
        out.append(alias)
    return tuple(out)


def normalize_kind(raw: object) -> str:
    kind = str(raw or "").strip().lower()
    return kind if kind in ROLE_KINDS else ""


def role_def_from_mapping(row: dict[str, Any]) -> RoleDef:
    rid = normalize_role_id(row.get("id") or row.get("role_id"))
    kind = normalize_kind(row.get("kind"))
    if not rid:
        raise ValueError("role id is required (kebab-case)")
    if not kind:
        raise ValueError(f"role {rid!r}: kind must be engagement-pm|analyzer|technical")
    aliases = normalize_aliases(row.get("aliases"))
    aliases = tuple(a for a in aliases if a != rid)
    return RoleDef(
        id=rid,
        kind=kind,
        aliases=aliases,
        protected=bool(row.get("protected")),
        pending=bool(row.get("pending")),
        goal="",
        backstory=str(row.get("backstory") or "").strip(),
    )


def seed_role_text(role_id: str, kind: str) -> tuple[str, str]:
    """Return ``(goal, backstory)`` for first create — goal always empty."""
    rid = normalize_role_id(role_id)
    for role in DEFAULT_ROLES:
        if role.id == rid:
            return "", role.backstory
    for role in DEFAULT_ROLES:
        if role.kind == kind and kind in BOOKEND_KINDS:
            return "", role.backstory
    return (
        "",
        "You are a technical specialist. Stay inside the rules of engagement.",
    )


def load_role_catalog() -> tuple[RoleDef, ...]:
    """Active roles from the platform catalog (defaults when DB unavailable)."""
    try:
        from peon.projects.platform_catalog import PlatformCatalog

        return PlatformCatalog.shared().list_roles()
    except Exception:
        return DEFAULT_ROLES


def catalog_role_ids() -> list[str]:
    return [r.id for r in load_role_catalog()]


def role_alias_map(roles: Iterable[RoleDef] | None = None) -> dict[str, str]:
    rows = tuple(roles) if roles is not None else load_role_catalog()
    out: dict[str, str] = dict(ROLE_ID_REMAP)
    for row in rows:
        out[row.id] = row.id
        for alias in row.aliases:
            out[alias] = row.id
    return out


def resolve_role_id(role_id: str, roles: Iterable[RoleDef] | None = None) -> str:
    rid = (role_id or "").strip().lower()
    if not rid:
        return ""
    return role_alias_map(roles).get(rid, rid)


def catalog_role(role_id: str) -> RoleDef | None:
    rid = resolve_role_id(role_id)
    for row in load_role_catalog():
        if row.id == rid:
            return row
    return None
