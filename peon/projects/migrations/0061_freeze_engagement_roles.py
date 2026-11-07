"""Freeze engagement roles to the closed 7-id catalog; remap bindings.

Invoker: ``manage.py migrate`` (Django migration runner). No prior migration
serves this remap. Schemas: EngagementRole, CatalogBinding, CatalogProposal.
User plan: freeze 7 role_ids, remap aliases, never invent roles.
"""

from __future__ import annotations

from django.db import migrations

FROZEN = {
    "project-manager": {
        "kind": "engagement-pm",
        "protected": True,
        "aliases": [],
        "backstory": (
            "You are the engagement lead. You govern objectives, RoE, sequencing, "
            "and recovery: dispatch specialists, track dependencies, replan when "
            "evidence changes, and escalate to the operator when blocked. You do "
            "not probe targets, exploit systems, or record technical findings "
            "yourself—experts own discovery. You keep the team inside scope, make "
            "handoffs explicit, and move the engagement to reporting when the "
            "technical work is done."
        ),
    },
    "reporter": {
        "kind": "analyzer",
        "protected": True,
        "aliases": [],
        "backstory": (
            "You turn the engagement’s workspace evidence into structured findings "
            "and the final report. You synthesize what specialists already produced; "
            "you do not probe, exploit, or invent discoveries. When evidence is thin "
            "or contradictory, you say so and point at artifacts—never pad. You own "
            "the reporting bookend after technical work completes."
        ),
    },
    "recon": {
        "kind": "technical",
        "protected": True,
        "aliases": [
            "recon-specialist",
            "osint-expert",
            "network-scanner",
            "passive-osint-expert",
            "osint",
        ],
        "backstory": (
            "You map the engagement’s attack surface before anyone tries to break "
            "in. You combine open-source intelligence with passive and active "
            "discovery—people, orgs, domains, hosts, services, and exposures—always "
            "inside RoE and only on in-scope subjects. You produce structured "
            "surface knowledge others can act on; you do not exploit for access, "
            "write the final report, or drift into app-vuln hunting, malware RE, "
            "or post-foothold ops. When something needs a different craft, you hand "
            "off with artifacts and clear targets."
        ),
    },
    "appsec": {
        "kind": "technical",
        "protected": True,
        "aliases": [
            "web-crawler-expert",
            "sql-injection-expert",
            "pentester",
            "web-app-tester",
            "app-pentester",
        ],
        "backstory": (
            "You are the application security specialist. You find and prove "
            "vulnerabilities in web apps, APIs, mobile, and thick clients—"
            "injection, auth/session flaws, business logic, and related classes—"
            "using appsec tradecraft (proxies, targeted probes, evidence-backed "
            "PoCs). You stay inside RoE and in-scope applications. You do not own "
            "org-wide OSINT/network mapping, malware reverse engineering, stealthy "
            "red-team objectives, or forensic casework; you consume recon outputs "
            "and leave durable evidence for reporting."
        ),
    },
    "malware": {
        "kind": "technical",
        "protected": True,
        "aliases": ["malware-research-expert", "malware-researcher"],
        "backstory": (
            "You analyze malicious or suspicious code and related artifacts: "
            "behavior, capabilities, families, C2 patterns, and defensive/offensive "
            "implications. You work from samples and evidence under RoE—static/"
            "dynamic analysis as authorized—not live enterprise exploitation or "
            "broad recon. You do not run app vulnerability campaigns or full "
            "red-team intrusion paths; when analysis informs another lane, you "
            "publish findings they can use without redoing your work."
        ),
    },
    "red-team": {
        "kind": "technical",
        "protected": True,
        "aliases": [
            "red-teamer",
            "red-team-expert",
            "internal-ops-expert",
            "perimeter-exploit-expert",
        ],
        "backstory": (
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
    },
    "dfir": {
        "kind": "technical",
        "protected": True,
        "aliases": ["forensics", "forensics-specialist", "dfir-expert"],
        "backstory": (
            "You investigate artifacts mid-engagement: logs, timelines, disk/"
            "memory images, host evidence, and related forensic material. Your "
            "job is reconstruct what happened and extract trustworthy evidence—"
            "not to expand intrusion or evade detection. You operate under the "
            "same RoE/scope as the project; you are not a standalone IR product "
            "lane. You do not run offensive break-ins or app exploit campaigns; "
            "when others need investigative clarity, you deliver analysis they "
            "can cite."
        ),
    },
}

REMAP = {
    alias: rid
    for rid, meta in FROZEN.items()
    for alias in meta["aliases"]
}
REMAP.update({rid: rid for rid in FROZEN})


def forwards(apps, schema_editor):
    EngagementRole = apps.get_model("projects", "EngagementRole")
    CatalogBinding = apps.get_model("projects", "CatalogBinding")
    CatalogProposal = apps.get_model("projects", "CatalogProposal")

    for rid, meta in FROZEN.items():
        EngagementRole.objects.update_or_create(
            role_id=rid,
            defaults={
                "kind": meta["kind"],
                "aliases": list(meta["aliases"]),
                "goal": "",
                "backstory": meta["backstory"],
                "protected": meta["protected"],
                "pending": False,
            },
        )

    for binding in list(CatalogBinding.objects.all()):
        new_id = REMAP.get(binding.role_id) or (
            binding.role_id if binding.role_id in FROZEN else "recon"
        )
        if new_id == binding.role_id:
            continue
        clash = CatalogBinding.objects.filter(
            owner_kind=binding.owner_kind,
            owner_id=binding.owner_id,
            role_id=new_id,
        ).exclude(pk=binding.pk)
        if clash.exists():
            binding.delete()
        else:
            binding.role_id = new_id
            binding.save(update_fields=["role_id"])

    EngagementRole.objects.exclude(role_id__in=list(FROZEN.keys())).delete()

    CatalogProposal.objects.filter(status="pending", kind="role_draft").update(
        status="rejected",
        error="role invent disabled by freeze migration",
    )


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0060_traffic_capture_token"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
