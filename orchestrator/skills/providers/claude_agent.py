"""Claude Agent Skills packs under the imported root."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from orchestrator.skills.manifest import ParseProfile, SkillParseError, parse_skill_manifest
from orchestrator.skills.providers.base import (
    CLAUDE_PROVIDER,
    DISCLOSURE_PROGRESSIVE,
    PEON_PROVIDER,
    SkillDocument,
    SkillId,
    SkillProvider,
)
from orchestrator.skills.providers.setup import (
    find_setup,
    infer_skill_affinity,
    manifest_document_fields,
)
from orchestrator.skills.providers.tool_bridge import classify_claude_tools
from orchestrator.skills.registry_scan import discover_skill_candidates

logger = logging.getLogger(__name__)


class ClaudeAgentSkillsProvider(SkillProvider):
    name = CLAUDE_PROVIDER
    disclosure = DISCLOSURE_PROGRESSIVE

    def __init__(self, root: Path, *, container_base: str = "/skills-imported") -> None:
        super().__init__(root, container_base=container_base)

    def discover(self) -> Sequence[SkillDocument]:
        root = self.root
        if not root.is_dir():
            return ()
        docs: list[SkillDocument] = []
        for candidate in discover_skill_candidates(
            root,
            source="imported",
            skip_helpers=False,
        ):
            try:
                # Claude-compatible field set; Peon still registers by command name.
                spec = parse_skill_manifest(
                    candidate.manifest,
                    profile=ParseProfile.CLAUDE_CODE,
                )
            except SkillParseError as exc:
                logger.warning(
                    "imported skill skipped %s: %s",
                    candidate.manifest,
                    exc.message,
                )
                continue
            except OSError as exc:
                logger.warning(
                    "imported skill unreadable %s: %s",
                    candidate.manifest,
                    exc,
                )
                continue
            parsed = SkillId.parse(spec.id)
            # Frontmatter ``name`` is bare. A slash means it is not a skill id.
            if parsed is None or parsed.provider != PEON_PROVIDER:
                logger.warning("imported skill invalid id %s", spec.id)
                continue
            if not parsed.name:
                continue
            if candidate.root.name.lower() != parsed.name:
                logger.info(
                    "imported skill dir %s registers as %s",
                    candidate.root.name,
                    parsed.name,
                )
            _, unmapped = classify_claude_tools(spec.tools)
            affinity = infer_skill_affinity(candidate.root, manifest=spec)
            roles = affinity.roles_above()
            scores = tuple(
                (binding.role_id, binding.score)
                for binding in affinity.bindings
                if binding.role_id in roles
            )
            setup, setup_root = find_setup(candidate.root, self.root)
            docs.append(
                SkillDocument(
                    skill_id=SkillId(provider=CLAUDE_PROVIDER, name=parsed.name),
                    description=spec.description,
                    instructions=spec.instructions,
                    root=candidate.root,
                    tools=tuple(spec.tools),
                    allow_binaries=tuple(spec.allow_binaries),
                    tags=tuple(spec.tags),
                    disclosure=DISCLOSURE_PROGRESSIVE,
                    role_ids=roles,
                    lead_roles=tuple(r for r in affinity.lead_roles if r in roles),
                    affinity_scores=scores,
                    compatibility=spec.compatibility,
                    **manifest_document_fields(spec),
                    setup=setup,
                    unmapped_tools=unmapped,
                    container_root=self.container_path(candidate.root),
                    container_setup_root=(
                        self.container_path(setup_root) if setup_root else ""
                    ),
                )
            )
        return docs
