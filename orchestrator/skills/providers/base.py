"""Skill provider contract shared by every skill style."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.skills.common import SKILL_NAME_RE, render_prompt_block

PEON_PROVIDER = "peon"
CLAUDE_PROVIDER = "claude"

DISCLOSURE_EAGER = "eager"
DISCLOSURE_PROGRESSIVE = "progressive"

AFFINITY_NAME = "peon.affinity.yaml"


@dataclass(frozen=True)
class SkillId:
    """``provider`` + skill ``name``. Bare strings address the Peon provider."""

    provider: str
    name: str

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.name}"

    @property
    def public(self) -> str:
        if self.provider == PEON_PROVIDER:
            return self.name
        return self.key

    @classmethod
    def parse(cls, raw: object) -> SkillId | None:
        text = str(raw or "").strip().replace("\\", "/")
        if not text or text.startswith("/") or "//" in text:
            return None
        if "/" in text:
            provider, _, name = text.partition("/")
        else:
            provider, name = PEON_PROVIDER, text
        provider = provider.strip().lower()
        name = name.strip().lower()
        if not SKILL_NAME_RE.match(provider) or not SKILL_NAME_RE.match(name):
            return None
        return cls(provider=provider, name=name)


@dataclass(frozen=True)
class SkillSetup:
    """Structured install hint. Never scraped from skill prose."""

    kind: str
    cwd: str = "."
    manifest: str = ""
    # One-off commands run after the dependency install (declared by the skill).
    post: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillDocument:
    """Normalized pack both providers return."""

    skill_id: SkillId
    description: str
    instructions: str
    root: Path | None
    tools: tuple[str, ...] = ()
    allow_binaries: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    disclosure: str = DISCLOSURE_EAGER
    role_ids: tuple[str, ...] = ()
    lead_roles: tuple[str, ...] = ()
    affinity_scores: tuple[tuple[str, float], ...] = ()
    compatibility: str = ""
    setup: SkillSetup | None = None
    unmapped_tools: tuple[str, ...] = ()
    # Absolute path inside the runtime. Host ``root`` is never interpolated
    # into a command, because external skill roots may live outside Peon's
    # repository and are mounted at a different location in Docker.
    container_root: str = ""
    container_setup_root: str = ""
    license: str = ""
    when_to_use: str = ""
    argument_hint: str = ""
    arguments: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    disable_model_invocation: bool = False
    user_invocable: bool = True
    model: str = ""
    context: str = ""
    agent: str = ""
    background: bool = True
    paths: tuple[str, ...] = ()
    shell: str = ""
    hooks: dict[str, object] | None = None
    raw_frontmatter: dict[str, object] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.skill_id.public

    @property
    def provider(self) -> str:
        return self.skill_id.provider

    @property
    def name(self) -> str:
        return self.skill_id.name

    def access_error(self, *, role_id: str = "") -> str:
        """Role-gated access. Matches ``SkillRegistry.usable``.

        Empty ``role_id`` skips the role check for peon (catalog/Toolsmith), but
        imported skills always require a bound role.
        """
        role = (role_id or "").strip().lower()
        if self.provider == PEON_PROVIDER:
            if not role:
                return ""
            if role not in self.role_ids:
                return f"Error: skill {self.id} is not bound to role {role}."
            return ""
        if not self.role_ids:
            return f"Error: skill {self.id} is not bound to any role."
        if not role:
            return f"Error: skill {self.id} requires a bound role."
        if role not in self.role_ids:
            return f"Error: skill {self.id} is not bound to role {role}."
        return ""

    def prompt_block(self, *, body_limit: int = 8000) -> str:
        return render_prompt_block(
            self.id, self.description, self.instructions, body_limit=body_limit
        )
