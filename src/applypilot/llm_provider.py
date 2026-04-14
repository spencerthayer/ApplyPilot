"""Shared LLM provider metadata and env-driven detection helpers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMProviderSpec:
    """Static configuration for a supported LLM provider."""

    key: str
    label: str
    env_key: str
    default_model: str
    base_url: str | None = None


@dataclass(frozen=True)
class LLMProviderSelection:
    """Resolved provider configuration from the current environment."""

    spec: LLMProviderSpec
    base_url: str
    model: str
    api_key: str


LLM_PROVIDER_SPECS: dict[str, LLMProviderSpec] = {
    "gemini": LLMProviderSpec(
        key="gemini",
        label="Gemini",
        env_key="GEMINI_API_KEY",
        default_model="gemini-2.0-flash",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    ),
    "openrouter": LLMProviderSpec(
        key="openrouter",
        label="OpenRouter",
        env_key="OPENROUTER_API_KEY",
        default_model="google/gemini-2.0-flash-001",
        base_url="https://openrouter.ai/api/v1",
    ),
    "openai": LLMProviderSpec(
        key="openai",
        label="OpenAI",
        env_key="OPENAI_API_KEY",
        default_model="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
    ),
    "anthropic": LLMProviderSpec(
        key="anthropic",
        label="Anthropic",
        env_key="ANTHROPIC_API_KEY",
        default_model="claude-3-5-haiku-latest",
        base_url="https://api.anthropic.com/v1",
    ),
    "local": LLMProviderSpec(
        key="local",
        label="Local",
        env_key="LLM_URL",
        default_model="local-model",
    ),
    "bedrock": LLMProviderSpec(
        key="bedrock",
        label="AWS Bedrock",
        env_key="BEDROCK_MODEL_ID",
        default_model="anthropic.claude-3-haiku-20240307-v1:0",
    ),
}

REMOTE_PROVIDER_ORDER = ("gemini", "openrouter", "openai", "anthropic")
PROVIDER_DETECTION_ORDER = ("local", *REMOTE_PROVIDER_ORDER, "bedrock")
WIZARD_PROVIDER_ORDER = ("gemini", "openrouter", "openai", "anthropic", "bedrock", "local")


def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def detect_llm_provider(environ: Mapping[str, str] | None = None) -> LLMProviderSelection | None:
    """Return the first configured provider using the repo's precedence rules."""

    env = _env(environ)
    model_override = env.get("LLM_MODEL", "").strip()

    local_url = env.get("LLM_URL", "").strip()
    if local_url:
        spec = LLM_PROVIDER_SPECS["local"]
        return LLMProviderSelection(
            spec=spec,
            base_url=local_url.rstrip("/"),
            model=model_override or spec.default_model,
            api_key=env.get("LLM_API_KEY", "").strip(),
        )

    for provider_key in REMOTE_PROVIDER_ORDER:
        spec = LLM_PROVIDER_SPECS[provider_key]
        api_key = env.get(spec.env_key, "").strip()
        if api_key:
            return LLMProviderSelection(
                spec=spec,
                base_url=spec.base_url or "",
                model=model_override or spec.default_model,
                api_key=api_key,
            )

    # Bedrock: no API key — uses boto3 credential chain (ada credentials update)
    bedrock_model = env.get("BEDROCK_MODEL_ID", "").strip()
    if bedrock_model:
        spec = LLM_PROVIDER_SPECS["bedrock"]
        return LLMProviderSelection(
            spec=spec,
            base_url="",
            model=model_override or bedrock_model,
            api_key="",
        )

    return None


def has_llm_provider(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether any supported LLM provider is configured."""

    return detect_llm_provider(environ) is not None


def format_llm_provider_status(environ: Mapping[str, str] | None = None) -> str | None:
    """Return a short human-readable provider summary for doctor/status output."""

    selection = detect_llm_provider(environ)
    if selection is None:
        return None

    if selection.spec.key == "local":
        return f"Local: {selection.base_url}"

    if selection.spec.key == "bedrock":
        region = (_env(environ)).get("BEDROCK_REGION", "us-east-1").strip()
        return f"AWS Bedrock ({selection.model}) in {region}"

    return f"{selection.spec.label} ({selection.model})"


def llm_config_hint() -> str:
    """Return the canonical guidance string for missing LLM setup."""

    return (
        "Set GEMINI_API_KEY, OPENROUTER_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, "
        "BEDROCK_MODEL_ID, or LLM_URL in ~/.applypilot/.env (run 'applypilot init')"
    )
