"""Platform-agnostic runtime configuration (env / dataclass — no Django)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from orchestrator.utils.strings import as_bool
from orchestrator.prompts import FINDINGS_GUIDANCE, INSTALL_CASCADE

# Peon engagement default; host may override via configure(...).
_DEFAULT_SYSTEM_PREAMBLE = (
    "You are a Peon job agent for an authorized engagement.\n"
    "Use only bound tools.\n"
    "Prefer run_skill_script for catalog skills; run_cli only for ad-hoc shell.\n"
    f"Call provision_cli before missing CLIs ({INSTALL_CASCADE}).\n"
    "Do not search the filesystem for skill scripts — invoke them via run_skill_script.\n"
    "Stay in-scope; do not expand RoE. Be concise.\n"
    "Obey OPERATOR INSTRUCTION / FOLLOW-UP messages when they appear.\n"
    f"{FINDINGS_GUIDANCE}"
)
_DEFAULT_LLM_PROVIDER = "openrouter"
_DEFAULT_LITELLM_MODEL = "openrouter/openai/gpt-4o-mini"
# Public SoT for provider → env var (also used by peon apps bootstrap / llm utils).
LLM_PROVIDER_KEY_ENV = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def _env_path(key: str, default: str | Path) -> Path:
    raw = _env(key)
    return Path(raw).expanduser().resolve() if raw else Path(default).expanduser().resolve()


def _env_list(key: str) -> list[str]:
    raw = _env(key)
    if not raw:
        return []
    return [p.strip() for p in raw.replace(";", ":").split(":") if p.strip()]


@dataclass
class RuntimeConfig:
    """Paths, LLM, agent, and sandbox settings for one process."""

    skills_dir: Path = field(default_factory=lambda: Path("skills").resolve())
    skills_external_dirs: list[Path] = field(default_factory=list)
    tools_catalog_dir: Path = field(default_factory=lambda: Path("tools/catalog").resolve())
    workspaces_dir: Path = field(default_factory=lambda: Path("workspaces").resolve())

    llm_provider: str = _DEFAULT_LLM_PROVIDER
    litellm_model: str = _DEFAULT_LITELLM_MODEL
    litellm_api_key: str = ""
    litellm_api_base: str | None = None
    llm_temperature: float = 0.0
    llm_max_tokens: int | None = None

    agent_max_iterations: int = 40
    agent_max_failure_replans: int = 2
    agent_max_subagents: int = 4
    agent_max_subagent_depth: int = 2
    agent_runtime_enabled: bool = True

    sandbox_enabled: bool = True
    sandbox_image: str = "peon-sandbox:local"
    sandbox_prefix: str = "peon-project"
    sandbox_remove_on_exit: bool = False

    system_preamble: str = _DEFAULT_SYSTEM_PREAMBLE

    def ensure_dirs(self) -> None:
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.tools_catalog_dir.mkdir(parents=True, exist_ok=True)
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)

    def apply_env(self) -> None:
        """Export path hints used by catalog / docker exec helpers."""
        os.environ["SKILLS_DIR"] = str(self.skills_dir)
        os.environ["TOOLS_CATALOG_DIR"] = str(self.tools_catalog_dir)
        os.environ["PROJECT_WORKSPACES_DIR"] = str(self.workspaces_dir)
        if self.litellm_api_key:
            provider = (self.llm_provider or "").strip().lower()
            key_env = LLM_PROVIDER_KEY_ENV.get(provider, "LITELLM_API_KEY")
            os.environ.setdefault(key_env, self.litellm_api_key)
            os.environ.setdefault("LITELLM_API_KEY", self.litellm_api_key)

    @classmethod
    def from_env(cls, *, root: Path | None = None) -> RuntimeConfig:
        base = (root or Path.cwd()).resolve()
        provider = _env("LLM_PROVIDER", _DEFAULT_LLM_PROVIDER)
        key_attr = LLM_PROVIDER_KEY_ENV.get(provider, "LITELLM_API_KEY")
        api_key = (
            _env(key_attr)
            or _env("LITELLM_API_KEY")
            or _env("OPENROUTER_API_KEY")
            or _env("OPENAI_API_KEY")
        )
        max_tokens_raw = _env("LLM_MAX_TOKENS")
        max_tokens = int(max_tokens_raw) if max_tokens_raw.isdigit() else None
        external = [
            Path(os.path.expandvars(os.path.expanduser(p))).resolve()
            for p in _env_list("SKILLS_EXTERNAL_DIRS")
        ]
        return cls(
            skills_dir=_env_path("SKILLS_DIR", base / "skills"),
            skills_external_dirs=external,
            tools_catalog_dir=_env_path("TOOLS_CATALOG_DIR", base / "tools" / "catalog"),
            workspaces_dir=_env_path("PROJECT_WORKSPACES_DIR", base / "workspaces"),
            llm_provider=provider,
            litellm_model=_env("LITELLM_MODEL")
            or _env("LLM_MODEL")
            or _DEFAULT_LITELLM_MODEL,
            litellm_api_key=api_key,
            litellm_api_base=_env("LITELLM_API_BASE") or None,
            llm_temperature=float(_env("LLM_TEMPERATURE", "0") or 0),
            llm_max_tokens=max_tokens,
            agent_max_iterations=int(_env("AGENT_MAX_ITERATIONS", "40") or 40),
            agent_max_failure_replans=int(_env("AGENT_MAX_FAILURE_REPLANS", "2") or 2),
            agent_max_subagents=int(_env("AGENT_MAX_SUBAGENTS", "4") or 4),
            agent_max_subagent_depth=int(_env("AGENT_MAX_SUBAGENT_DEPTH", "2") or 2),
            agent_runtime_enabled=as_bool(_env("AGENT_RUNTIME_ENABLED", "true"), default=True),
            sandbox_enabled=as_bool(_env("SANDBOX_ENABLED", "true"), default=True),
            sandbox_image=_env("SANDBOX_IMAGE", "peon-sandbox:local"),
            sandbox_prefix=_env("PROJECT_SANDBOX_PREFIX")
            or _env("SANDBOX_PREFIX", "peon-project"),
            sandbox_remove_on_exit=as_bool(
                _env("SANDBOX_REMOVE_ON_EXIT", "false"), default=False
            ),
            system_preamble=_env("ORCHESTRATOR_SYSTEM_PREAMBLE") or _DEFAULT_SYSTEM_PREAMBLE,
        )

    def merge(self, **overrides: object) -> RuntimeConfig:
        return replace(self, **overrides)  # type: ignore[arg-type]


def discover_root(start: Path | str | None = None) -> Path:
    """Find a project root that contains ``skills/`` and ``tools/catalog/``."""
    here = Path(start or Path.cwd()).resolve()
    candidates = [here, *here.parents]
    try:
        import orchestrator as _pkg

        pkg = Path(_pkg.__file__).resolve()
        # In-tree: <repo>/orchestrator/__init__.py → parents[1]
        # src-layout: <repo>/src/orchestrator/__init__.py → parents[2]
        candidates.extend([pkg.parents[1], pkg.parents[2]])
    except Exception:
        pass
    for path in candidates:
        if (path / "skills").is_dir() and (path / "tools" / "catalog").is_dir():
            return path
    return here


_config: RuntimeConfig | None = None


def get_config() -> RuntimeConfig:
    global _config
    if _config is None:
        _config = RuntimeConfig.from_env(root=discover_root())
        _config.apply_env()
    return _config


def configure(cfg: RuntimeConfig | None = None, **overrides: object) -> RuntimeConfig:
    """Set process-wide config. Call once at startup from the host app."""
    global _config
    base = cfg or _config or RuntimeConfig.from_env()
    if overrides:
        base = base.merge(**overrides)
    base.ensure_dirs()
    base.apply_env()
    _config = base
    try:
        from orchestrator.skills.misc.registry import SkillRegistry
        from orchestrator.tools.catalog.catalog import ToolCatalog

        SkillRegistry.reset_shared()
        ToolCatalog.reset_shared()
    except Exception:
        pass
    return _config
