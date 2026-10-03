"""In-process LiteLLM provider module (default Peon backend)."""

from __future__ import annotations

import os
from typing import Any, ClassVar

from langchain_core.language_models import BaseChatModel
from langchain_litellm import ChatLiteLLM

from orchestrator.config import LLM_PROVIDER_KEY_ENV, get_config
from orchestrator.llm.provider_base import LLMProviderBase


class LiteLLMProvider(LLMProviderBase):
    """ChatLiteLLM adapter — talks upstream (OpenRouter, etc.) in-process."""

    id: ClassVar[str] = "litellm"

    def configured(self) -> bool:
        cfg = get_config()
        return bool((cfg.litellm_api_key or "").strip())

    def chat_model(self) -> BaseChatModel:
        cfg = get_config()
        provider = (cfg.llm_provider or "").strip().lower()
        if (env := LLM_PROVIDER_KEY_ENV.get(provider)) and cfg.litellm_api_key:
            os.environ.setdefault(env, cfg.litellm_api_key)
        opts: dict[str, Any] = {
            "model": cfg.litellm_model,
            "temperature": float(cfg.llm_temperature or 0.0),
        }
        if cfg.litellm_api_key:
            opts["api_key"] = cfg.litellm_api_key
        if cfg.litellm_api_base:
            opts["api_base"] = cfg.litellm_api_base
        if cfg.llm_max_tokens:
            opts["max_tokens"] = cfg.llm_max_tokens
        return ChatLiteLLM(**opts)

    def _describe_extra(self) -> dict[str, Any]:
        cfg = get_config()
        return {
            "upstream": (cfg.llm_provider or "").strip().lower(),
            "model": cfg.litellm_model,
            "api_base": cfg.litellm_api_base,
        }
