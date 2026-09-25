"""LLM config, chat model, and short invoke helpers (LiteLLM — no Django)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_litellm import ChatLiteLLM

from orchestrator.config import LLM_PROVIDER_KEY_ENV, get_config
from orchestrator.utils.strings import extract_json, plain_text

LLM_NOT_CONFIGURED = (
    "LLM not configured — set OPENROUTER_API_KEY (or OpenAI/LiteLLM)."
)


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    temperature: float
    api_key: str
    api_base: str | None = None
    max_tokens: int | None = None


def llm_config() -> LLMConfig:
    cfg = get_config()
    return LLMConfig(
        provider=cfg.llm_provider,
        model=cfg.litellm_model,
        temperature=float(cfg.llm_temperature or 0.0),
        api_key=cfg.litellm_api_key or "",
        api_base=cfg.litellm_api_base or None,
        max_tokens=cfg.llm_max_tokens,
    )


def llm_configured() -> bool:
    return bool((llm_config().api_key or "").strip())


def require_llm() -> None:
    if not llm_configured():
        raise RuntimeError(LLM_NOT_CONFIGURED)


def chat_model() -> BaseChatModel:
    cfg = llm_config()
    if (env := LLM_PROVIDER_KEY_ENV.get(cfg.provider)) and cfg.api_key:
        os.environ.setdefault(env, cfg.api_key)
    opts: dict = {"model": cfg.model, "temperature": cfg.temperature}
    if cfg.api_key:
        opts["api_key"] = cfg.api_key
    if cfg.api_base:
        opts["api_base"] = cfg.api_base
    if cfg.max_tokens:
        opts["max_tokens"] = cfg.max_tokens
    return ChatLiteLLM(**opts)


def chat_text(system: str, human: str) -> str:
    resp = chat_model().invoke(
        [SystemMessage(content=system), HumanMessage(content=human)]
    )
    return plain_text(getattr(resp, "content", resp))


def chat_json(system: str, human: str) -> dict:
    return json.loads(extract_json(chat_text(system, human)))
