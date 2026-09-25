"""Skill draft helpers: catalog resolution, SKILL.md normalize, run.py ensure."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from orchestrator.capabilities import REGISTRY, default_allowed_tools, ensure_registered
from orchestrator.skills.misc.utils import (
    MAX_COMPATIBILITY_LEN,
    LintIssue,
    coerce_lifecycle,
    dump_skill_md,
    extract_file_refs,
    normalize_category,
    parse_frontmatter_yaml,
    truncate_compat,
    valid_skill_name,
)
from orchestrator.skills.provision import METADATA_TOP_LEVEL_FIELDS, SkillLinter
from orchestrator.tools.catalog import ToolCatalog
from orchestrator.utils.install_docs import has_install_fence
from orchestrator.utils.strings import as_str_list, unique

RUN_PY = "scripts/run.py"
INSTALL_GUIDE = "references/INSTALL.md"
_RUN_BINARY_RE = re.compile(r'run_binary\(\s*["\']([^"\']+)["\']')
_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9._+-]{1,31}")
_GENERIC_ALLOWED = frozenset({"bash", "read", "write"})
_EXEC_ALLOWED = frozenset({"run_skill_script", "run_cli", "provision_cli"})
# Same install taxonomy as tools-suggestor / CatalogProvisioner.
CATALOG_INSTALL_TYPES = ("apt", "github_release", "git_clone", "pip", "custom")

# Re-export for authoring callers.
__all__ = [
    "CATALOG_INSTALL_TYPES",
    "CatalogResolution",
    "INSTALL_GUIDE",
    "RUN_PY",
    "authoring_prompt",
    "catalog_summaries",
    "ensure_skill_entry",
    "install_guide_markdown",
    "lint_draft",
    "parse_skill_payload",
    "prompt_catalog_hits",
    "proposed_tool_match",
    "valid_skill_name",
]


@dataclass
class CatalogResolution:
    matched: list[dict[str, str]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def mode(self) -> str:
        return "catalog_wrapper" if self.matched else "capability"

    @property
    def primary_binary(self) -> str:
        return self.matched[0]["binary"] if self.matched else ""


def catalog_summaries(*, limit: int = 80) -> list[dict[str, str]]:
    return ToolCatalog.shared().summaries(limit=limit)


def resolve_catalog_keys(keys: list[str]) -> CatalogResolution:
    matched: list[dict[str, str]] = []
    missing: list[str] = []
    seen_keys: set[str] = set()
    seen_ids: set[str] = set()
    catalog = ToolCatalog.shared()
    for raw in keys:
        key = (raw or "").strip().lower()
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        tool = catalog.lookup(key)
        if tool is None:
            missing.append(key)
            continue
        if tool.id in seen_ids:
            continue
        seen_ids.add(tool.id)
        matched.append(
            {
                "id": tool.id,
                "binary": (tool.binary or tool.id).strip(),
                "description": (tool.description or "")[:240],
            }
        )
    return CatalogResolution(matched=matched, missing=missing)


def prompt_catalog_hits(prompt: str) -> list[dict[str, str]]:
    """Catalog tools named in free text (match-only; ignores unknown tokens)."""
    return resolve_catalog_keys(
        [t.lower() for t in _TOKEN_RE.findall(prompt or "")]
    ).matched


def proposed_tool_match(suggestion: dict[str, Any]) -> dict[str, str]:
    """Normalize a tools-suggestor result into a catalog hit shape."""
    parsed = suggestion.get("parsed") if isinstance(suggestion.get("parsed"), dict) else {}
    tid = str(suggestion.get("id") or parsed.get("id") or "").strip()
    binary = str(parsed.get("binary") or tid).strip() or tid
    desc = str(parsed.get("description") or suggestion.get("notes") or "").strip()
    return {"id": tid, "binary": binary, "description": desc[:240]}


def install_guide_markdown(
    *, yaml_text: str = "", notes: str = "", binary: str = ""
) -> str:
    """Skill ``references/INSTALL.md`` body for InstallResolver step #2."""
    block: dict[str, Any] = {}
    text = (yaml_text or "").strip()
    if text:
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError:
            parsed = None
        if isinstance(parsed, dict):
            if isinstance(parsed.get("install"), list):
                block["install"] = parsed["install"]
            if isinstance(parsed.get("verify"), list):
                block["verify"] = parsed["verify"]
            if not binary:
                binary = str(parsed.get("binary") or parsed.get("id") or "").strip()
    if not block.get("install"):
        raise ValueError("install recipe YAML must include an install: list")
    fence = yaml.safe_dump(block, sort_keys=False).rstrip()
    note = (notes or "").strip()
    cli = (binary or "").strip() or "the CLI"
    lines = [
        f"# Install ({cli})",
        "",
        "Used by `provision_cli` / `InstallResolver` when this binary is not in "
        "`tools/catalog` (cascade: catalog → skill docs → LLM).",
        "",
    ]
    if note:
        lines.extend([note, ""])
    lines.extend(["```yaml", fence, "```", ""])
    return "\n".join(lines)


def _cli_tokens(raw: Any) -> list[str]:
    out: list[str] = []
    for item in as_str_list(raw):
        out.extend(p.lower() for p in re.split(r"[\s,;]+", item) if p.strip())
    return unique(out)


def _clis_from_frontmatter(data: dict[str, Any]) -> list[str]:
    """CLI ids from ``requires_clis`` / ``toolkit`` only — never free prose."""
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    clis = _cli_tokens(meta.get("requires_clis") or meta.get("toolkit"))
    if not clis:
        clis = _cli_tokens(data.get("requires_clis") or data.get("toolkit"))
    return clis


def _merge_matched(
    primary: CatalogResolution, extra: CatalogResolution
) -> CatalogResolution:
    """Union matched tools; keep ``missing`` only from ``primary`` (declared CLIs)."""
    seen = {m["id"] for m in primary.matched}
    matched = list(primary.matched)
    for item in extra.matched:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        matched.append(item)
    return CatalogResolution(matched=matched, missing=list(primary.missing))


def resolve_intent(
    *,
    skill_md: str,
    suggested_tools: list[str],
    prompt: str,
    proposed: list[dict[str, str]] | None = None,
) -> CatalogResolution:
    """Resolve catalog tools for a draft.

    Declared CLIs (``requires_clis`` / ``suggested_tools``) are looked up on disk.
    Prompt tokens are match-only. ``proposed`` entries (from tools-suggestor, not
    yet on disk) count as matched so the skill can wrap them as catalog_wrapper.
    """
    declared: list[str] = []
    parsed = parse_frontmatter_yaml(skill_md)
    if parsed:
        declared.extend(_clis_from_frontmatter(parsed[0]))
    declared.extend(t.strip().lower() for t in suggested_tools if t.strip())
    resolution = resolve_catalog_keys(declared)

    prompt_tokens = [t.lower() for t in _TOKEN_RE.findall(prompt or "")]
    if prompt_tokens:
        prompt_hits = CatalogResolution(
            matched=resolve_catalog_keys(prompt_tokens).matched, missing=[]
        )
        resolution = _merge_matched(resolution, prompt_hits)

    proposed_hits: list[dict[str, str]] = []
    proposed_ids: set[str] = set()
    for item in proposed or []:
        tid = str(item.get("id") or "").strip()
        if not tid or tid in proposed_ids:
            continue
        proposed_ids.add(tid)
        proposed_hits.append(
            {
                "id": tid,
                "binary": str(item.get("binary") or tid).strip() or tid,
                "description": str(item.get("description") or "")[:240],
            }
        )
    if proposed_hits:
        resolution = _merge_matched(
            resolution, CatalogResolution(matched=proposed_hits, missing=[])
        )
        # Declared ids covered by a proposed recipe are not "missing".
        resolution = CatalogResolution(
            matched=resolution.matched,
            missing=[m for m in resolution.missing if m not in proposed_ids],
        )
    return resolution


def _coerce_allowed_tools(raw: Any, *, mode: str) -> str:
    default = default_allowed_tools(mode=mode)
    if isinstance(raw, list):
        tokens = [str(x).strip() for x in raw if str(x).strip()]
    elif raw is None:
        tokens = []
    else:
        tokens = [t for t in str(raw).split() if t.strip()]
    lower = {t.lower() for t in tokens}
    if (
        not tokens
        or lower.issubset(_GENERIC_ALLOWED)
        or not (lower & _EXEC_ALLOWED)
        or (mode == "capability" and "run_cli" not in lower)
    ):
        return default
    ensure_registered()
    registered = set(REGISTRY.tool_map())
    kept = [t for t in tokens if t in registered]
    if mode == "catalog_wrapper" and "run_skill_script" not in kept:
        return default
    missing = [t for t in default.split() if t not in kept]
    return " ".join(unique([*kept, *missing])) if missing else " ".join(kept) or default


def normalize_skill_md(
    skill_md: str, *, name: str, mode: str = "catalog_wrapper"
) -> str:
    parsed = parse_frontmatter_yaml(skill_md)
    if not parsed:
        return skill_md
    data, body = parsed
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    for key in list(data.keys()):
        if key in METADATA_TOP_LEVEL_FIELDS and key != "metadata":
            val = data.pop(key)
            if key == "taskable" and "jobable" not in meta:
                meta["jobable"] = val
            elif key not in meta:
                meta[key] = val
    data["metadata"] = meta
    if not str(data.get("name") or "").strip():
        data["name"] = name
    data["allowed-tools"] = _coerce_allowed_tools(data.get("allowed-tools"), mode=mode)
    meta["lifecycle"] = coerce_lifecycle(meta.get("lifecycle"), allow_auto=True)
    if meta.get("lifecycle") == "auto":
        meta["lifecycle"] = "short"

    if meta.get("jobable") is True:
        meta["jobable"] = "true"
    elif meta.get("jobable") is False:
        meta["jobable"] = "false"
    elif "jobable" not in meta:
        meta["jobable"] = "true"

    if meta.get("category") is not None and str(meta.get("category") or "").strip():
        slug = normalize_category(meta["category"])
        if slug:
            meta["category"] = slug

    compat = data.get("compatibility")
    if isinstance(compat, str):
        data["compatibility"] = truncate_compat(compat, limit=MAX_COMPATIBILITY_LEN)
    return dump_skill_md(data, body)


def lint_draft(
    skill_md: str, files: dict[str, str], *, mode: str = "catalog_wrapper"
) -> dict[str, Any]:
    issues = list(SkillLinter.lint_text(skill_md, skill_dir=None))
    known = set(files)
    for ref in extract_file_refs(skill_md):
        if ref not in known:
            issues.append(
                LintIssue("error", "broken_ref", f"File reference `{ref}` not found.")
            )
    run_body = str(files.get(RUN_PY) or "").strip()
    if mode == "catalog_wrapper" and not run_body:
        issues.append(
            LintIssue(
                "error",
                "missing_run_script",
                "Missing required file: scripts/run.py "
                "(catalog CLI skills use run_skill_script → scripts/run.py).",
            )
        )
    parsed = parse_frontmatter_yaml(skill_md)
    at = str((parsed[0] if parsed else {}).get("allowed-tools") or "").lower()
    if mode == "capability" and "run_cli" not in at:
        issues.append(
            LintIssue(
                "warning",
                "capability_tools_missing",
                "Capability skill should allow run_cli.",
            )
        )
    elif (
        mode == "catalog_wrapper"
        and "run_skill_script" in skill_md
        and run_body
        and RUN_PY not in skill_md
    ):
        issues.append(
            LintIssue(
                "warning",
                "run_script_undocumented",
                "scripts/run.py exists but SKILL.md does not mention it.",
            )
        )
    declared = _clis_from_frontmatter(parsed[0]) if parsed else []
    missing_registry = [
        c for c in declared if ToolCatalog.shared().lookup(c) is None
    ]
    if missing_registry:
        guide = str(files.get(INSTALL_GUIDE) or "")
        if not has_install_fence(guide):
            issues.append(
                LintIssue(
                    "error",
                    "missing_install_guide",
                    f"CLI(s) {missing_registry} not in tools/catalog — add "
                    f"{INSTALL_GUIDE} with an ```yaml install: …``` fence "
                    "(InstallResolver step #2).",
                )
            )
        elif INSTALL_GUIDE not in skill_md and "INSTALL.md" not in skill_md:
            issues.append(
                LintIssue(
                    "warning",
                    "install_guide_undocumented",
                    f"{INSTALL_GUIDE} exists but SKILL.md does not link it.",
                )
            )
    return {
        "compatible": not any(i.level == "error" for i in issues),
        "errors": [i.to_dict() for i in issues if i.level == "error"],
        "warnings": [i.to_dict() for i in issues if i.level == "warning"],
    }


def run_binary_wrapper(binary: str) -> str:
    cli = (binary or "tool").strip() or "tool"
    return (
        "#!/usr/bin/env python3\n"
        "from skill_entry import main, run_binary\n\n\n"
        "def _run() -> int:\n"
        "    return run_binary(\n"
        f'        "{cli}",\n'
        f'        default_for_target=lambda t: f"{cli} {{t}}",\n'
        "    )\n\n\n"
        'if __name__ == "__main__":\n'
        "    main(_run)\n"
    )


def _ensure_run_section(
    skill_md: str, *, name: str, mode: str, provision_clis: list[str] | None = None
) -> str:
    if mode == "capability":
        if "run_cli" in skill_md:
            return skill_md
        exec_caps = [
            c
            for c in default_allowed_tools(mode="capability").split()
            if c in {"provision_cli", "run_cli"}
        ]
        lines = "\n".join(f'{c}("…")' for c in exec_caps) or 'run_cli("…")'
        return (
            f"{skill_md.rstrip()}\n\n## Run\n"
            "No matching `tools/catalog` CLI — use agent sandbox tools:\n"
            f"```\nsandbox_setup()\n{lines}\n```\n"
        )

    clis = [c for c in (provision_clis or []) if c]
    if RUN_PY in skill_md and "run_skill_script" in skill_md:
        if clis and "provision_cli" not in skill_md:
            prov = "\n".join(f'provision_cli("{c}")' for c in clis)
            return (
                f"{skill_md.rstrip()}\n\n## Provision\n"
                "Call before first use when the CLI is not yet on PATH "
                f"(catalog → `{INSTALL_GUIDE}` → LLM):\n"
                f"```\n{prov}\n```\n"
            )
        return skill_md

    run_lines = ["sandbox_setup()"]
    if clis:
        run_lines.extend(f'provision_cli("{c}")' for c in clis)
    run_lines.append(
        f'run_skill_script("{name}", "scripts/run.py", command="<cli args>")'
    )
    parts = [
        skill_md.rstrip(),
        "",
        "## Scripts",
        "- scripts/run.py — entry via run_binary",
    ]
    if clis:
        parts.extend(
            [
                "",
                "## Install",
                f"See [{INSTALL_GUIDE}]({INSTALL_GUIDE}) when the CLI is not in "
                "`tools/catalog`.",
            ]
        )
    parts.extend(["", "## Run", "```", *run_lines, "```", ""])
    return "\n".join(parts)


def _ensure_install_guide(
    files: dict[str, str],
    skill_md: str,
    *,
    recipes: list[dict[str, Any]],
) -> tuple[str, dict[str, str]]:
    """Inject ``references/INSTALL.md`` from tools-suggestor recipes when needed."""
    out = dict(files)
    if not recipes:
        return skill_md, out
    guide = str(out.get(INSTALL_GUIDE) or "")
    if not has_install_fence(guide):
        sections: list[str] = []
        for recipe in recipes:
            yaml_text = str(recipe.get("yaml") or "").strip()
            if not yaml_text:
                continue
            sections.append(
                install_guide_markdown(
                    yaml_text=yaml_text,
                    notes=str(recipe.get("notes") or ""),
                    binary=str(recipe.get("id") or ""),
                )
            )
        if sections:
            out[INSTALL_GUIDE] = "\n".join(sections).rstrip() + "\n"
    if INSTALL_GUIDE not in skill_md and "INSTALL.md" not in skill_md:
        skill_md = (
            f"{skill_md.rstrip()}\n\n## Install\n\n"
            f"When the required CLI is not in `tools/catalog`, see "
            f"[{INSTALL_GUIDE}]({INSTALL_GUIDE}) "
            f"(used by `provision_cli` / InstallResolver).\n"
        )
    return skill_md, out


def ensure_skill_entry(
    skill_md: str,
    files: dict[str, str],
    *,
    name: str,
    suggested_tools: list[str],
    prompt: str,
    proposed: list[dict[str, str]] | None = None,
    install_recipes: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, str], str]:
    """Catalog / proposed CLI → ``run_binary`` wrapper + optional INSTALL.md.

    ``install_recipes`` (tools-suggestor YAML) become ``references/INSTALL.md``
    for InstallResolver step #2 when the CLI is not in ``tools/catalog``.
    """
    out = dict(files)
    recipes = list(install_recipes or [])
    resolution = resolve_intent(
        skill_md=skill_md,
        suggested_tools=suggested_tools,
        prompt=prompt,
        proposed=proposed,
    )
    mode = resolution.mode
    run_src = str(out.get(RUN_PY) or "").strip()
    matched_bins = {m["binary"].lower() for m in resolution.matched} | {
        m["id"].lower() for m in resolution.matched
    }
    proposed_ids = {
        str(p.get("id") or p.get("binary") or "").strip().lower()
        for p in (proposed or [])
        if str(p.get("id") or p.get("binary") or "").strip()
    }

    if mode == "catalog_wrapper":
        wrapped = _RUN_BINARY_RE.search(run_src)
        wrong = bool(wrapped and wrapped.group(1).strip().lower() not in matched_bins)
        if not run_src or wrong:
            out[RUN_PY] = run_binary_wrapper(resolution.primary_binary)
    elif run_src:
        wrapped = _RUN_BINARY_RE.search(run_src)
        if wrapped:
            bin_name = wrapped.group(1).strip().lower()
            if (
                bin_name
                and ToolCatalog.shared().lookup(bin_name) is None
                and bin_name not in proposed_ids
            ):
                out.pop(RUN_PY, None)

    if recipes:
        skill_md, out = _ensure_install_guide(out, skill_md, recipes=recipes)

    off_catalog = [
        m["binary"] or m["id"]
        for m in resolution.matched
        if ToolCatalog.shared().lookup(m["id"]) is None
        and ToolCatalog.shared().lookup(m["binary"]) is None
    ]
    skill_md = normalize_skill_md(skill_md, name=name, mode=mode)
    skill_md = _ensure_run_section(
        skill_md, name=name, mode=mode, provision_clis=off_catalog or list(proposed_ids)
    )
    return skill_md, out, mode


def parse_skill_payload(
    payload: dict[str, Any],
) -> tuple[str, str, dict[str, str], list[str]]:
    name = str(payload.get("name") or "").strip().lower()
    skill_md = str(payload.get("skill_md") or "").strip()
    files_raw = payload.get("files") or {}
    files = (
        {str(k): str(v) for k, v in files_raw.items()}
        if isinstance(files_raw, dict)
        else {}
    )
    suggested = [
        str(t).strip()
        for t in (payload.get("suggested_tools") or [])
        if str(t).strip()
    ]
    if not valid_skill_name(name):
        raise RuntimeError(f"invalid skill name {name!r}")
    if not skill_md:
        raise RuntimeError("skill-writer returned empty skill_md")
    return name, skill_md, files, suggested


def authoring_prompt(
    prompt: str,
    catalog: list[dict[str, str]],
    *,
    proposed: list[dict[str, str]] | None = None,
    tool_recipes: list[dict[str, Any]] | None = None,
) -> str:
    on_disk = prompt_catalog_hits(prompt)
    proposed = list(proposed or [])
    hits = list(on_disk)
    seen = {h["id"] for h in hits}
    for item in proposed:
        if item["id"] not in seen:
            hits.append(item)
            seen.add(item["id"])
    mode = "catalog_wrapper" if hits else "capability"
    install_types = ", ".join(CATALOG_INSTALL_TYPES)
    lines = [
        f"Operator request:\n{prompt}\n",
        f"Catalog tools matched on disk:\n"
        f"{yaml.safe_dump(on_disk or ['(none)'], sort_keys=False)}",
        f"Authoring mode: {mode}",
        f"Default allowed-tools:\n  {default_allowed_tools(mode=mode)}",
    ]
    if tool_recipes:
        lines.append(
            "CLI is NOT in tools/catalog yet. Host drafted install recipe(s) via "
            f"tools-suggestor (types: {install_types}).\n"
            "Author as catalog_wrapper:\n"
            f"- metadata.requires_clis / suggested_tools = recipe id(s)\n"
            "- files[\"scripts/run.py\"] with skill_entry.run_binary\n"
            f"- REQUIRED files[\"{INSTALL_GUIDE}\"] with a single ```yaml fence "
            "starting with `install:` (same steps as the recipe). "
            "InstallResolver uses this as step #2 when catalog misses.\n"
            "- Do NOT put apt/git/pip/custom install steps in SKILL.md body — "
            f"only link {INSTALL_GUIDE}.\n"
            f"- Document: sandbox_setup() → provision_cli(\"<id>\") → "
            "run_skill_script(...)\n"
            f"{yaml.safe_dump(tool_recipes, sort_keys=False)}"
        )
    elif mode == "catalog_wrapper":
        lines.append(
            "CLI is in tools/catalog. Emit run_binary wrapper; do not add "
            f"{INSTALL_GUIDE} (provision comes from the registry)."
        )
    else:
        lines.append(
            "No CLI required. Capability mode: run_cli / provision_cli "
            "only. Do not invent run_binary for unknown CLIs."
        )
    lines.append(f"Available catalog tools:\n{yaml.safe_dump(catalog, sort_keys=False)}")
    return "\n".join(lines)
