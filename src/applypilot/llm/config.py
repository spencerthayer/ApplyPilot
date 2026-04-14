"""LLM configuration types and resolution (LLD §12.1).

Resolves which provider/model to use based on env vars and tier.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, TypedDict

from applypilot.llm_provider import detect_llm_provider, llm_config_hint

log = logging.getLogger(__name__)

_PROVIDER_API_KEYS = {
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}
_PROVIDER_BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
}
_KNOWN_PROVIDER_PREFIXES = frozenset(
    {
        "anthropic",
        "azure",
        "deepseek",
        "gemini",
        "local",
        "ollama",
        "openai",
        "openai_compat",
        "openrouter",
        "vertex_ai",
    }
)
_STREAMING_TRUE_VALUES = {"1", "true", "yes"}


class LLMTier(StrEnum):
    """3-tier model routing: cheap for bulk, mid for quality output, premium for agentic."""

    CHEAP = "cheap"
    MID = "mid"
    PREMIUM = "premium"


@dataclass(frozen=True)
class LLMConfig:
    """LLM configuration consumed by LLMClient."""

    provider: str
    api_base: str | None
    model: str
    api_key: str
    base_url: str | None = None
    use_streaming: bool = False


@dataclass(frozen=True)
class ModelEntry:
    """A fallback-capable model target."""

    name: str
    provider: str
    base_url: str
    api_key: str


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class LiteLLMExtra(TypedDict, total=False):
    stop: str | list[str]
    top_p: float
    seed: int
    stream: bool
    response_format: dict[str, Any]
    tools: list[dict[str, Any]]
    tool_choice: str | dict[str, Any]
    fallbacks: list[str]


def _env_get(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "")
    return str(value).strip() if value is not None else ""


def normalize_model(provider: str, model: str) -> str:
    if provider == "local":
        provider = "openai"
    if provider == "bedrock":
        return model if model.startswith("bedrock/") else f"bedrock/{model}"
    if provider == "openrouter":
        return model if model.startswith("openrouter/") else f"openrouter/{model}"
    return model if "/" in model else f"{provider}/{model}"


def provider_from_model(model: str) -> str:
    provider, _, remainder = model.partition("/")
    if not provider or not remainder:
        raise RuntimeError("LLM_MODEL must include a provider prefix (for example 'openai/gpt-4o-mini').")
    return provider


def raw_model_name(model: str) -> str:
    _, sep, remainder = model.partition("/")
    return remainder if sep else model


def is_provider_qualified(model: str) -> bool:
    prefix, sep, remainder = model.partition("/")
    return bool(prefix and sep and remainder and prefix in _KNOWN_PROVIDER_PREFIXES)


def resolve_llm_config(
        env: Mapping[str, str] | None = None,
        quality: bool = False,
        tier: LLMTier | None = None,
) -> LLMConfig:
    """Resolve runtime LLM configuration.

    Tier precedence: explicit tier > quality bool > cheap default.
    """
    if tier is None:
        tier = LLMTier.MID if quality else LLMTier.CHEAP

    env_map = os.environ if env is None else env
    selection = detect_llm_provider(env_map)

    configured_model = ""
    match tier:
        case LLMTier.PREMIUM:
            configured_model = _env_get(env_map, "LLM_MODEL_PREMIUM") or _env_get(env_map, "LLM_MODEL_QUALITY")
        case LLMTier.MID:
            configured_model = _env_get(env_map, "LLM_MODEL_MID") or _env_get(env_map, "LLM_MODEL_QUALITY")
        case LLMTier.CHEAP:
            configured_model = ""
    if not configured_model:
        configured_model = _env_get(env_map, "LLM_MODEL")

    if configured_model and configured_model.startswith("bedrock/"):
        return LLMConfig(
            provider="bedrock", api_base=None, model=configured_model, api_key="", base_url=None, use_streaming=False
        )

    local_url = _env_get(env_map, "LLM_URL").rstrip("/")
    use_streaming = _env_get(env_map, "LLM_STREAMING_MODE").lower() in _STREAMING_TRUE_VALUES

    if selection is not None:
        selected_provider = selection.spec.key
        raw_model = configured_model or selection.model
        model = normalize_model(selected_provider, raw_model)
        api_base = local_url if selected_provider == "local" else None
        provider = "openai" if selected_provider == "local" else selected_provider
        api_key = selection.api_key
        base_url = selection.base_url

        if selected_provider == "bedrock":
            provider, api_key, api_base, base_url = "bedrock", "", None, None

        return LLMConfig(
            provider=provider,
            api_base=api_base,
            model=model,
            api_key=api_key,
            base_url=base_url,
            use_streaming=use_streaming,
        )

    if not configured_model:
        raise RuntimeError(f"No LLM provider configured. {llm_config_hint()}")

    provider = provider_from_model(configured_model)
    api_key = _env_get(env_map, _PROVIDER_API_KEYS.get(provider, "")) or _env_get(env_map, "LLM_API_KEY")
    api_base = local_url or None
    if not api_key and not api_base:
        env_hint = _PROVIDER_API_KEYS.get(provider, "LLM_API_KEY")
        raise RuntimeError(f"Missing credentials for LLM_MODEL '{configured_model}'. Set {env_hint} or LLM_API_KEY.")

    return LLMConfig(
        provider=provider,
        api_base=api_base,
        model=configured_model,
        api_key=api_key,
        base_url=api_base or _PROVIDER_BASE_URLS.get(provider),
        use_streaming=use_streaming,
    )
