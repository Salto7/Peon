"""In-process LiteLLM provider module (default Peon backend)."""

from __future__ import annotations

import logging
import os
from typing import Any, ClassVar

from litellm import Router

from orchestrator.config import LLM_PROVIDER_KEY_ENV, get_config
from orchestrator.llm.provider_base import LLMProviderBase
from orchestrator.utils.strings import plain_text

logger = logging.getLogger(__name__)

# Deployment name callers request from the router; litellm.Router's own
# fallbacks=[{PRIMARY: [...]}] mapping is what actually tries the next
# provider — see _get_router. Each configured LLM_FALLBACK_PROVIDERS entry
# becomes its own numbered deployment.
_PRIMARY = "default"
_COOLDOWN_SECONDS = 60
# Trip after this many failures within the cooldown window — litellm's own
# default (None) disables cooldowns entirely, which is wrong for a single-
# deployment router: one correctly-config'd-but-rate-limited primary would
# otherwise never "cool down" and free a fallback to carry the turn.
_ALLOWED_FAILS = 1


class LiteLLMProvider(LLMProviderBase):
    """``litellm.Router`` over the configured upstream — retries transient
    failures in-process instead of failing the agent's turn outright.
    """

    id: ClassVar[str] = "litellm"

    def __init__(self) -> None:
        self._router: Router | None = None

    def configured(self) -> bool:
        cfg = get_config()
        model = (cfg.litellm_model or "").strip()
        if not model:
            return False
        mid = model.lower()
        # Local Ollama (and other OpenAI-compatible bases) need no cloud API key.
        if mid.startswith(("ollama/", "ollama_chat/")):
            return True
        if (cfg.litellm_api_base or "").strip():
            return True
        return bool((cfg.litellm_api_key or "").strip())

    def _completion_opts(
        self, *, provider: str, model: str, api_key: str, api_base: str
    ) -> dict[str, Any]:
        cfg = get_config()
        if (env := LLM_PROVIDER_KEY_ENV.get(provider.strip().lower())) and api_key:
            os.environ.setdefault(env, api_key)
        opts: dict[str, Any] = {
            "model": model,
            "temperature": float(cfg.llm_temperature or 0.0),
        }
        if api_key:
            opts["api_key"] = api_key
        if api_base:
            opts["api_base"] = api_base
        if cfg.llm_max_tokens:
            opts["max_tokens"] = cfg.llm_max_tokens
        return opts

    def _get_router(self) -> Router:
        """Build once per process — ``LITELLM_MODEL``/key/base all require a
        restart to change (see ``peon/projects/runtime_settings.py``), so
        there is no live-config case to invalidate this cache for.
        """
        if self._router is None:
            cfg = get_config()
            model_list = [
                {
                    "model_name": _PRIMARY,
                    "litellm_params": self._completion_opts(
                        provider=cfg.llm_provider or "",
                        model=cfg.litellm_model,
                        api_key=cfg.litellm_api_key,
                        api_base=cfg.litellm_api_base or "",
                    ),
                }
            ]
            fallback_names: list[str] = []
            for i, entry in enumerate(cfg.llm_fallback_providers):
                model = str(entry.get("model") or "").strip()
                if not model:
                    logger.warning("LLM_FALLBACK_PROVIDERS[%d] has no model, skipping", i)
                    continue
                name = f"fallback_{i}"
                model_list.append(
                    {
                        "model_name": name,
                        "litellm_params": self._completion_opts(
                            provider=str(entry.get("provider") or ""),
                            model=model,
                            api_key=str(entry.get("api_key") or ""),
                            api_base=str(entry.get("api_base") or ""),
                        ),
                    }
                )
                fallback_names.append(name)
            router_kwargs: dict[str, Any] = {"model_list": model_list, "num_retries": 2}
            if fallback_names:
                # Primary trips its own cooldown after _ALLOWED_FAILS fails
                # within _COOLDOWN_SECONDS, which is what actually lets the
                # Router fall through to these instead of retrying the same
                # dead primary forever.
                router_kwargs["fallbacks"] = [{_PRIMARY: fallback_names}]
                router_kwargs["allowed_fails"] = _ALLOWED_FAILS
                router_kwargs["cooldown_time"] = _COOLDOWN_SECONDS
            self._router = Router(**router_kwargs)
        return self._router

    def complete(self, messages: list[dict[str, str]]) -> str:
        resp = self._get_router().completion(model=_PRIMARY, messages=messages)
        choice = (resp.choices or [None])[0]
        if choice is None:
            return ""
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None) if message is not None else None
        return plain_text(content)

    def _describe_extra(self) -> dict[str, Any]:
        cfg = get_config()
        return {
            "upstream": (cfg.llm_provider or "").strip().lower(),
            "model": cfg.litellm_model,
            "api_base": cfg.litellm_api_base,
        }
