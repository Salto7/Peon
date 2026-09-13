"""LLM config, chat model, and short invoke helpers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from django.conf import settings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_litellm import ChatLiteLLM

from orchestrator.utils.strings import extract_json, plain_text

_ENV_KEY = {"openrouter": "OPENROUTER_API_KEY", "openai": "OPENAI_API_KEY"}


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    temperature: float
    api_key: str
    api_base: str | None = None
    max_tokens: int | None = None


def llm_config() -> LLMConfig:
    provider = getattr(settings, "LLM_PROVIDER", "openrouter")
    key_attr = _ENV_KEY.get(provider, "LITELLM_API_KEY")
    return LLMConfig(
        provider=provider,
        model=(
            getattr(settings, "LITELLM_MODEL", None)
            or getattr(settings, "LLM_MODEL", None)
            or "openrouter/openai/gpt-4o-mini"
        ),
        temperature=float(getattr(settings, "LLM_TEMPERATURE", 0.0) or 0.0),
        api_key=(
            getattr(settings, key_attr, "")
            or getattr(settings, "LITELLM_API_KEY", "")
            or getattr(settings, "OPENROUTER_API_KEY", "")
            or ""
        ),
        api_base=getattr(settings, "LITELLM_API_BASE", None) or None,
        max_tokens=getattr(settings, "LLM_MAX_TOKENS", None),
    )


def chat_model() -> BaseChatModel:
    cfg = llm_config()
    if (env := _ENV_KEY.get(cfg.provider)) and cfg.api_key:
        os.environ.setdefault(env, cfg.api_key)
    opts = {"model": cfg.model, "temperature": cfg.temperature}
    if cfg.api_key:
        opts["api_key"] = cfg.api_key
    if cfg.api_base:
        opts["api_base"] = cfg.api_base
    if cfg.max_tokens:
        opts["max_tokens"] = cfg.max_tokens
    return ChatLiteLLM(**opts)


def chat_text(system: str, human: str) -> str:
    resp = chat_model().invoke([SystemMessage(content=system), HumanMessage(content=human)])
    return plain_text(getattr(resp, "content", resp))


def chat_json(system: str, human: str) -> dict:
    return json.loads(extract_json(chat_text(system, human)))
