"""First-party Claude Agent Skills under ``skills/<id>/``.

Same SKILL.md ABI as imported packs. Bare ids stay ``peon``.
Role membership is inferred from the skill and role catalog; RoE is enforced
at the engagement / sandbox command layer, not per skill.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from orchestrator.skills.manifest import ParseProfile, SkillParseError, parse_skill_manifest
from orchestrator.skills.providers.base import (
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


class PeonNativeProvider(SkillProvider):
    name = PEON_PROVIDER
    disclosure = DISCLOSURE_PROGRESSIVE

    def __init__(self, root: Path, *, container_base: str = "/skills") -> None:
        super().__init__(root, container_base=container_base)

    def discover(self) -> Sequence[SkillDocument]:
        docs: list[SkillDocument] = []
        for candidate in discover_skill_candidates(
            self.root,
            source="native",
            skip_helpers=True,
        ):
            try:
                spec = parse_skill_manifest(candidate.manifest, profile=ParseProfile.PEON)
            except SkillParseError as exc:
                logger.warning(
                    "native skill skipped %s: %s",
                    candidate.manifest,
                    exc.message,
                )
                continue
            except OSError as exc:
                logger.warning("native skill unreadable %s: %s", candidate.manifest, exc)
                continue
            skill_id = SkillId.parse(spec.id)
            if skill_id is None or skill_id.provider != PEON_PROVIDER:
                logger.warning("native skill invalid id %s", spec.id)
                continue
            if candidate.root.name.lower() != skill_id.name:
                logger.warning(
                    "native skill dir/name mismatch %s vs %s",
                    candidate.root.name,
                    skill_id.name,
                )
                continue
            _, unmapped = classify_claude_tools(spec.tools)
            affinity = infer_skill_affinity(candidate.root, manifest=spec)
            roles = affinity.roles_above()
            scores = tuple(
                (b.role_id, b.score)
                for b in affinity.bindings
                if b.role_id in roles
            )
            setup, setup_root = find_setup(candidate.root, self.root)
            docs.append(
                SkillDocument(
                    skill_id=SkillId(provider=PEON_PROVIDER, name=skill_id.name),
                    description=spec.description,
                    instructions=spec.instructions,
                    root=spec.root or candidate.root,
                    tools=tuple(spec.tools),
                    allow_binaries=tuple(spec.allow_binaries),
                    tags=tuple(spec.tags),
                    disclosure=DISCLOSURE_PROGRESSIVE,
                    role_ids=roles,
                    lead_roles=tuple(
                        r for r in affinity.lead_roles if r in roles
                    ),
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
