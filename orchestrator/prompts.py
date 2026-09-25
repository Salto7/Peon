"""Shared LLM prompt fragments (install cascade, agent recover, planner)."""

from __future__ import annotations

# Three-layer CLI install path (runtime + authoring + agent preamble).
INSTALL_CASCADE = "tools/catalog → skill references/INSTALL.md → LLM"

INSTALL_MISSING_HINT = (
    f"If missing: {INSTALL_CASCADE} via provision_cli."
)

# Last-resort LLM install recipe (aligned with skills/tools-suggestor PROMPT.md).
# Steps in ``install`` run in list order — put apt deps before git_clone / pip.
PLANNER_INSTALL_SYSTEM = (
    "Install a missing CLI in a minimal debian:bookworm-slim Docker sandbox "
    "(no git, no preinstalled runtime interpreters, or extra tools until your "
    "steps add them). "
    "Reply with JSON only, no prose: "
    '{"install":[{"type":"apt"|"github_release"|"git_clone"|"pip"|"custom",...}],'
    '"verify":[{"command":"..."}]}. '
    "List install steps in execution order. Prefer: apt when the tool is a Debian "
    "package; github_release for release binaries; apt then git_clone for cloned "
    "repos (git_clone needs repo, entrypoint, binary). For git_clone, make sure "
    "the runtime interpreter referenced by the entrypoint's `#!/usr/bin/env <name>` "
    "exists (add the corresponding apt dependency, and if needed also add/ensure "
    "a compatible alias). Use custom only as a last resort — inline shell must run "
    "apt-get for prerequisites itself. verify must succeed after all steps; use the "
    "binary name on PATH."
)

# Catalog recipe preference when drafting/revising tools/catalog YAML.
CATALOG_INSTALL_PREFER = (
    "Prefer apt when the package exists on Debian bookworm; else github_release; "
    "else apt then git_clone; else pip; use custom only as a last resort "
    "(custom scripts must apt-get their own prerequisites)."
)

AGENT_RECOVER_NUDGE = (
    "The last tool call failed. Call provision_cli for a missing CLI, adjust "
    "arguments, or use run_skill_script — then retry. Do not repeat the identical "
    "failing call."
)

# Engagement findings vs run/objective status (agent preamble + tools).
FINDINGS_GUIDANCE = (
    "Findings are engagement discoveries about any in-scope subject "
    "(network assets, files, malware/samples, source, packages, identities, "
    "cloud resources, or other producer-labeled assets), each with evidence. "
    "kind and asset_type are free-form slugs — not a fixed taxonomy. "
    "Do NOT record findings for job/objective/agent progress or skill completion; "
    "use update_objective_status for objectives and stream/logs for run status."
)

