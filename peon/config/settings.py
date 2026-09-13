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

# Production data lives under data/ (never the repo-root sqlite used by old local runs).
_DATA_DIR = Path(_env("PEON_DATA_DIR") or (BASE_DIR / "data")).resolve()
_DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(_DATA_DIR / "db.sqlite3"),
    }
}

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
STATIC_ROOT = _DATA_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

_skills = Path(_env("SKILLS_DIR") or (BASE_DIR / "skills"))
SKILLS_DIR = _skills if _skills.is_absolute() else (BASE_DIR / _skills).resolve()
SKILLS_EXTERNAL_DIRS: list[str] = []

_tools_catalog = Path(_env("TOOLS_CATALOG_DIR") or (BASE_DIR / "tools" / "catalog"))
TOOLS_CATALOG_DIR = (
    _tools_catalog if _tools_catalog.is_absolute() else (BASE_DIR / _tools_catalog).resolve()
)
os.environ.setdefault("TOOLS_CATALOG_DIR", str(TOOLS_CATALOG_DIR))

# One directory per Project under this root; sandboxes bind only that folder.
PROJECT_WORKSPACES_DIR = Path(
    _env("PROJECT_WORKSPACES_DIR") or (_DATA_DIR / "project_workspaces")
).resolve()
PROJECT_WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)

_stream_raw = _env("STREAM_SOCKET_PATH") or "/tmp/peon/stream.sock"
_stream_path = Path(_stream_raw)
if not _stream_path.is_absolute() or ".." in _stream_path.parts:
    _stream_path = Path("/tmp/peon/stream.sock")
STREAM_SOCKET_PATH = str(_stream_path)
Path(STREAM_SOCKET_PATH).parent.mkdir(parents=True, exist_ok=True)

# true = one Docker container per Project; false = one shared Docker sandbox.
SANDBOX_ENABLED = _env_bool("SANDBOX_ENABLED", True)
PROJECT_SANDBOX_PREFIX = _env("PROJECT_SANDBOX_PREFIX", "peon-project")
SANDBOX_IMAGE = _env("SANDBOX_IMAGE", "peon-sandbox:local")
# Named Docker volume for stream sockets when the worker runs in Compose.
SANDBOX_SOCKETS_VOLUME = _env("SANDBOX_SOCKETS_VOLUME", "")

# Isolated Learn-page install lab (not project sandboxes / SANDBOX_IMAGE).
LEARN_LAB_IMAGE = _env("LEARN_LAB_IMAGE", "debian:bookworm-slim")
LEARN_LAB_CONTAINER = _env("LEARN_LAB_CONTAINER", "peon-learn-lab")

# Dramatiq + Redis (Compose adds redis + worker). Disable in tests (StubBroker).
REDIS_URL = _env("REDIS_URL", "redis://127.0.0.1:6379/0")
DRAMATIQ_ENABLED = _env_bool("DRAMATIQ_ENABLED", True)
# Soft wall-clock for one actor run (ms) — long OSINT/pentest jobs.
DRAMATIQ_TIME_LIMIT_MS = int(_env("DRAMATIQ_TIME_LIMIT_MS", str(6 * 60 * 60 * 1000)) or 0)
DRAMATIQ_MAX_RETRIES = int(_env("DRAMATIQ_MAX_RETRIES", "3") or 3)

LLM_PROVIDER = _env("LLM_PROVIDER", "openrouter")
LITELLM_MODEL = _env("LITELLM_MODEL", "openrouter/openai/gpt-4o-mini")
LITELLM_API_BASE = _env("LITELLM_API_BASE") or None
LITELLM_API_KEY = _env("LITELLM_API_KEY")
OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")
OPENAI_API_KEY = _env("OPENAI_API_KEY")
LLM_TEMPERATURE = float(_env("LLM_TEMPERATURE", "0") or "0")
_max_tokens = _env("LLM_MAX_TOKENS").strip()
LLM_MAX_TOKENS = int(_max_tokens) if _max_tokens.isdigit() else None

# Agent runtime governors (LangGraph job loop — peon passes these into orchestrator).
# Overridable live via Peon Settings UI (RuntimeSettings); .env values are defaults.
AGENT_MAX_FAILURE_REPLANS = max(0, int(_env("AGENT_MAX_FAILURE_REPLANS", "2") or 2))
AGENT_MAX_ITERATIONS = max(1, int(_env("AGENT_MAX_ITERATIONS", "40") or 40))
AGENT_MAX_SUBAGENTS = max(0, int(_env("AGENT_MAX_SUBAGENTS", "4") or 4))
AGENT_MAX_SUBAGENT_DEPTH = max(1, int(_env("AGENT_MAX_SUBAGENT_DEPTH", "2") or 2))
# Emergency kill switch (default on). Jobs use orchestrator.agent; false fails closed.
AGENT_RUNTIME_ENABLED = _env_bool("AGENT_RUNTIME_ENABLED", True)

# Parallel dispatch caps (live via Settings UI; Dramatiq threads need worker restart).
MAX_PARALLEL_PROJECTS = max(1, int(_env("MAX_PARALLEL_PROJECTS", "3") or 3))
MAX_AGENTS_PER_PROJECT = max(1, int(_env("MAX_AGENTS_PER_PROJECT", "2") or 2))
DRAMATIQ_THREADS = max(1, int(_env("DRAMATIQ_THREADS", "4") or 4))
