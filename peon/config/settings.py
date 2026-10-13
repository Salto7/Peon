"""Django settings for Peon (production-oriented defaults)."""

from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


_DEFAULT_SECRET = "peon-change-me-insecure"
SECRET_KEY = _env("SECRET_KEY", _DEFAULT_SECRET)
DEBUG = _env_bool("DEBUG", False)
ALLOWED_HOSTS = [h for h in _env("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
if DEBUG and "testserver" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append("testserver")

if not DEBUG and SECRET_KEY in {_DEFAULT_SECRET, "peon-dev-only", ""}:
    raise ImproperlyConfigured(
        "Set a strong SECRET_KEY in the environment when DEBUG is false."
    )

PUBLIC_URL = _env("PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")
_secure = PUBLIC_URL.lower().startswith("https://")
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = _secure
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in _env(
        "CSRF_TRUSTED_ORIGINS",
        "https://localhost:8000,https://127.0.0.1:8000",
    ).split(",")
    if o.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "peon.projects.apps.ProjectsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "peon.config.urls"
WSGI_APPLICATION = "peon.config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "peon" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# One workspace root: ``platform`` is reserved; UUID children belong to Projects.
PROJECT_WORKSPACES_DIR = Path(
    _env("PROJECT_WORKSPACES_DIR") or (BASE_DIR / "workspaces")
).resolve()
PROJECT_WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
PLATFORM_WORKSPACE_DIR = (PROJECT_WORKSPACES_DIR / "platform").resolve()
PLATFORM_WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(PLATFORM_WORKSPACE_DIR / "db.sqlite3"),
        "OPTIONS": {"timeout": 30},
    }
}
DATABASE_ROUTERS = ["peon.projects.db.PeonDatabaseRouter"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "peon" / "static"]
STATIC_ROOT = PLATFORM_WORKSPACE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

_skills = Path(_env("SKILLS_DIR") or (BASE_DIR / "skills"))
SKILLS_DIR = _skills if _skills.is_absolute() else (BASE_DIR / _skills).resolve()
os.environ.setdefault("SKILLS_DIR", str(SKILLS_DIR))
_skills_imported = Path(_env("SKILLS_IMPORTED_DIR") or (BASE_DIR / "skills_imported"))
SKILLS_IMPORTED_DIR = (
    _skills_imported
    if _skills_imported.is_absolute()
    else (BASE_DIR / _skills_imported).resolve()
)
os.environ.setdefault("SKILLS_IMPORTED_DIR", str(SKILLS_IMPORTED_DIR))


def _env_paths(name: str) -> tuple[Path, ...]:
    raw = _env(name).replace(";", ":")
    paths: list[Path] = []
    for item in raw.split(":"):
        value = item.strip()
        if not value:
            continue
        path = Path(value).expanduser()
        paths.append(path if path.is_absolute() else (BASE_DIR / path).resolve())
    return tuple(paths)


# Optional Claude/Agent-Skills roots outside Peon's managed import directory.
# They are discovered by the catalog and mounted read-only into project Docker
# sandboxes. Keep the setting empty unless the root is trusted.
SKILLS_EXTRA_DIRS = _env_paths("SKILLS_EXTRA_DIRS")
_helpers = Path(_env("HELPERS_DIR") or (BASE_DIR / "helpers"))
HELPERS_DIR = _helpers if _helpers.is_absolute() else (BASE_DIR / _helpers).resolve()
os.environ.setdefault("HELPERS_DIR", str(HELPERS_DIR))

_tools_catalog = Path(_env("TOOLS_CATALOG_DIR") or (BASE_DIR / "tools" / "catalog"))
TOOLS_CATALOG_DIR = (
    _tools_catalog if _tools_catalog.is_absolute() else (BASE_DIR / _tools_catalog).resolve()
)
os.environ.setdefault("TOOLS_CATALOG_DIR", str(TOOLS_CATALOG_DIR))

_stream_raw = _env("STREAM_SOCKET_PATH") or "/tmp/peon/stream.sock"
_stream_path = Path(_stream_raw)
if not _stream_path.is_absolute() or ".." in _stream_path.parts:
    _stream_path = Path("/tmp/peon/stream.sock")
STREAM_SOCKET_PATH = str(_stream_path)
Path(STREAM_SOCKET_PATH).parent.mkdir(parents=True, exist_ok=True)

PROJECT_SANDBOX_PREFIX = _env("PROJECT_SANDBOX_PREFIX", "peon-project")
# Kali-based job sandbox (build: compose --profile build build sandbox).
SANDBOX_IMAGE = _env("SANDBOX_IMAGE", "peon-sandbox:local")
# OpenShell uses the same Kali image by default (override separately if needed).
OPENSHELL_IMAGE = _env("OPENSHELL_IMAGE", SANDBOX_IMAGE) or SANDBOX_IMAGE
# Named Docker volume for the stream socket when the worker runs in Compose.
SANDBOX_SOCKETS_VOLUME = _env("SANDBOX_SOCKETS_VOLUME", "")

# Isolated Learn-page install lab — same Kali family as project sandboxes.
LEARN_LAB_IMAGE = _env("LEARN_LAB_IMAGE", SANDBOX_IMAGE) or SANDBOX_IMAGE
LEARN_LAB_CONTAINER = _env("LEARN_LAB_CONTAINER", "peon-learn-lab")

# Dramatiq + Redis (Compose adds redis + worker). Disable in tests (StubBroker).
REDIS_URL = _env("REDIS_URL", "redis://127.0.0.1:6379/0")
DRAMATIQ_ENABLED = _env_bool("DRAMATIQ_ENABLED", True)
# Soft wall-clock for one actor run (ms) — long OSINT/pentest jobs.
DRAMATIQ_TIME_LIMIT_MS = int(_env("DRAMATIQ_TIME_LIMIT_MS", str(6 * 60 * 60 * 1000)) or 0)
DRAMATIQ_MAX_RETRIES = int(_env("DRAMATIQ_MAX_RETRIES", "3") or 3)

LLM_PROVIDER = _env("LLM_PROVIDER", "openrouter")
# Provider module id (in-process backend). Default litellm; reserved for future modules.
LLM_MODULE = _env("LLM_MODULE", "litellm") or "litellm"
LITELLM_MODEL = _env("LITELLM_MODEL", "openrouter/openai/gpt-5.6-luna")
LITELLM_API_BASE = _env("LITELLM_API_BASE") or None
LITELLM_API_KEY = _env("LITELLM_API_KEY")
OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")
OPENAI_API_KEY = _env("OPENAI_API_KEY")
LLM_TEMPERATURE = float(_env("LLM_TEMPERATURE", "0") or "0")
_max_tokens = _env("LLM_MAX_TOKENS").strip()
LLM_MAX_TOKENS = int(_max_tokens) if _max_tokens.isdigit() else None
# OpenAI-compatible LiteLLM proxy — Lab OpenCode only (Cognee calls OpenRouter directly).
# Intent is live-overridable via Peon Settings (LLM_PROXY_ENABLED); enabling starts
# the peon-litellm container on the Compose network.
LLM_PROXY_ENABLED = _env_bool("LLM_PROXY_ENABLED", True)
LLM_PROXY_URL = _env("LLM_PROXY_URL", "http://litellm:4000/v1") or "http://litellm:4000/v1"
LLM_PROXY_PORT = int(_env("LLM_PROXY_PORT", "4000") or 4000)
LLM_PROXY_CONTAINER = _env("LLM_PROXY_CONTAINER", "peon-litellm") or "peon-litellm"
LLM_PROXY_IMAGE = _env(
    "LLM_PROXY_IMAGE", "ghcr.io/berriai/litellm:main-stable"
) or "ghcr.io/berriai/litellm:main-stable"
LLM_PROXY_NETWORK = _env("LLM_PROXY_NETWORK", "peon_default") or "peon_default"
LITELLM_MASTER_KEY = _env("LITELLM_MASTER_KEY", "sk-peon-litellm") or "sk-peon-litellm"
# OpenCode model id (provider/model) used inside the Learn lab.
OPENCODE_MODEL = _env("OPENCODE_MODEL", "peon/default") or "peon/default"

# Agent runtime governors (peon passes these into orchestrator).
# Overridable live via Peon Settings UI (RuntimeSettings); .env values are defaults.
AGENT_MAX_FAILURE_REPLANS = max(0, int(_env("AGENT_MAX_FAILURE_REPLANS", "2") or 2))
AGENT_MAX_ITERATIONS = max(1, int(_env("AGENT_MAX_ITERATIONS", "40") or 40))
AGENT_MAX_SUBAGENTS = max(0, int(_env("AGENT_MAX_SUBAGENTS", "8") or 8))
AGENT_MAX_SUBAGENT_DEPTH = max(1, int(_env("AGENT_MAX_SUBAGENT_DEPTH", "2") or 2))
# Emergency kill switch (default on). Jobs use orchestrator.agent; false fails closed.
AGENT_RUNTIME_ENABLED = _env_bool("AGENT_RUNTIME_ENABLED", True)
# CrewAI PlanningConfig when RoleSpec.reasoning is true.
_CREW_EFFORT = (_env("CREW_REASONING_EFFORT", "low") or "low").strip().lower()
CREW_REASONING_EFFORT = (
    _CREW_EFFORT if _CREW_EFFORT in {"low", "medium", "high"} else "low"
)
CREW_REASONING_MAX_ATTEMPTS = max(
    1, min(5, int(_env("CREW_REASONING_MAX_ATTEMPTS", "1") or 1))
)
# Cognee self-hosted memory sidecar (semantic recall + dreaming).
COGNEE_ENABLED = _env_bool("COGNEE_ENABLED", True)
COGNEE_PORT = int(_env("COGNEE_PORT", "8001") or 8001)
# Derived from COGNEE_PORT — do not set a separate base URL in .env.
COGNEE_API_BASE = f"http://cognee:{COGNEE_PORT}"
COGNEE_HTTP_TIMEOUT_S = float(_env("COGNEE_HTTP_TIMEOUT_S", "120") or "120")
# Cognee sidecar LLM = LITELLM_MODEL via OpenRouter (compose env). Embeddings are
# separate (cognify requires vectors) — OpenRouter text-embedding-3-small by default.
COGNEE_EMBEDDING_MODEL = (
    _env("COGNEE_EMBEDDING_MODEL", "openrouter/openai/text-embedding-3-small")
    or "openrouter/openai/text-embedding-3-small"
)
COGNEE_EMBEDDING_DIMENSIONS = int(_env("COGNEE_EMBEDDING_DIMENSIONS", "1536") or 1536)
# Cross-project peon dataset (bootstrap recall on new projects).
PEON_MEMORY_ENABLED = _env_bool("PEON_MEMORY_ENABLED", True)
# Background Cognee improve() cycles.
PEON_DREAMING_ENABLED = _env_bool("PEON_DREAMING_ENABLED", True)
PEON_DREAMING_INTERVAL_S = max(60, int(_env("PEON_DREAMING_INTERVAL_S", "900") or 900))
# Compose / edge (exposed for Peon Settings defaults).
WEB_PORT = int(_env("WEB_PORT", "8000") or 8000)
PUBLIC_HOST = _env("PUBLIC_HOST", "localhost") or "localhost"
# Durable ProjectSchedule Dramatiq scheduler (fires due cron/interval rows).
DURABLE_SCHEDULER_ENABLED = _env_bool("DURABLE_SCHEDULER_ENABLED", True)
DURABLE_SCHEDULER_INTERVAL_MS = max(
    15_000, int(_env("DURABLE_SCHEDULER_INTERVAL_MS", "60000") or 60_000)
)
# Orchestration module (crewai only on Peon-crewAI).
AGENT_MODULE = (_env("AGENT_MODULE", "crewai") or "crewai").strip().lower()

# Parallel dispatch caps (live via Settings UI; Dramatiq threads need worker restart).
MAX_PARALLEL_PROJECTS = max(1, int(_env("MAX_PARALLEL_PROJECTS", "3") or 3))
# Root experts + spawn_work children + parallel objectives share this cap.
MAX_AGENTS_PER_PROJECT = max(1, int(_env("MAX_AGENTS_PER_PROJECT", "8") or 8))
# Max Dramatiq worker threads (--threads). Keep ≥ MAX_AGENTS_PER_PROJECT.
# Accept legacy DRAMATIQ_THREADS env as fallback.
_DRAMATIQ_THREADS_RAW = _env("DRAMATIQ_MAX_THREADS") or _env("DRAMATIQ_THREADS", "8")
DRAMATIQ_MAX_THREADS = max(1, int(_DRAMATIQ_THREADS_RAW or 8))
# Backward-compat alias for older imports / docs.
DRAMATIQ_THREADS = DRAMATIQ_MAX_THREADS
JOB_STUCK_RUNNING_SECONDS = max(
    60, int(_env("JOB_STUCK_RUNNING_SECONDS", "7200") or 7200)
)
