"""Unified LLM client for ApplyPilot using LiteLLM internally.

Public contract:
  - Provider detection stays anchored in applypilot.llm_provider.
  - OpenRouter remains a first-class user-facing provider.
  - LLMClient.chat() accepts both max_tokens and max_output_tokens.
"""

from __future__ import annotations

import logging
import os
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Literal, TypedDict

try:
    import litellm
except ModuleNotFoundError:  # pragma: no cover - exercised only in minimal environments
    def _missing_litellm(**_: Any) -> Any:
        raise RuntimeError("litellm is required for ApplyPilot LLM requests. Install project dependencies first.")

    litellm = SimpleNamespace(completion=_missing_litellm, suppress_debug_info=False)

from applypilot.llm_provider import detect_llm_provider, llm_config_hint

warnings.filterwarnings("ignore", category=UserWarning, module="pydantic.*")

log = logging.getLogger(__name__)

_MAX_RETRIES = 5
_TIMEOUT = 120
_DEFAULT_MAX_TOKENS = 4096
_STREAMING_TRUE_VALUES = {"1", "true", "yes"}
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
@dataclass(frozen=True)
class LLMConfig:
    """LLM configuration consumed by LLMClient."""

    provider: str
    api_base: str | None
    model: str
    api_key: str
    base_url: str | None = None
    use_streaming: bool = False


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
    if value is None:
        return ""
    return str(value).strip()


def _normalize_model(provider: str, model: str) -> str:
    if provider == "local":
        provider = "openai"
    if provider == "openrouter":
        return model if model.startswith("openrouter/") else f"openrouter/{model}"
    return model if "/" in model else f"{provider}/{model}"


def _provider_from_model(model: str) -> str:
    provider, _, remainder = model.partition("/")
    if not provider or not remainder:
        raise RuntimeError("LLM_MODEL must include a provider prefix (for example 'openai/gpt-4o-mini').")
    return provider


def _detect_provider() -> tuple[str, str, str]:
    """Return the canonical provider tuple expected by main-branch callers."""

    selection = detect_llm_provider()
    if selection is not None:
        return selection.base_url, selection.model, selection.api_key
    raise RuntimeError(f"No LLM provider configured. {llm_config_hint()}")


def resolve_llm_config(env: Mapping[str, str] | None = None) -> LLMConfig:
    """Resolve runtime LLM configuration while preserving main's provider contract."""

    env_map = os.environ if env is None else env
    selection = detect_llm_provider(env_map)
    configured_model = _env_get(env_map, "LLM_MODEL")
    local_url = _env_get(env_map, "LLM_URL").rstrip("/")
    use_streaming = _env_get(env_map, "LLM_STREAMING_MODE").lower() in _STREAMING_TRUE_VALUES

    if selection is not None:
        selected_provider = selection.spec.key
        raw_model = configured_model or selection.model
        model = _normalize_model(selected_provider, raw_model)
        api_base = local_url if selected_provider == "local" else None
        provider = "openai" if selected_provider == "local" else selected_provider
        return LLMConfig(
            provider=provider,
            api_base=api_base,
            model=model,
            api_key=selection.api_key,
            base_url=selection.base_url,
            use_streaming=use_streaming,
        )

    if not configured_model:
        raise RuntimeError(f"No LLM provider configured. {llm_config_hint()}")

    provider = _provider_from_model(configured_model)
    api_key = _env_get(env_map, _PROVIDER_API_KEYS.get(provider, "")) or _env_get(env_map, "LLM_API_KEY")
    api_base = local_url or None
    if not api_key and not api_base:
        env_hint = _PROVIDER_API_KEYS.get(provider, "LLM_API_KEY")
        raise RuntimeError(
            f"Missing credentials for LLM_MODEL '{configured_model}'. "
            f"Set {env_hint} or LLM_API_KEY, or provide LLM_URL for a local endpoint."
        )

    return LLMConfig(
        provider=provider,
        api_base=api_base,
        model=configured_model,
        api_key=api_key,
        base_url=api_base or _PROVIDER_BASE_URLS.get(provider),
        use_streaming=use_streaming,
    )


class LLMClient:
    """Thin wrapper around LiteLLM completion()."""

    def __init__(self, config_or_base_url: LLMConfig | str, model: str | None = None, api_key: str | None = None) -> None:
        if isinstance(config_or_base_url, LLMConfig):
            self.config = config_or_base_url
        else:
            base_url = config_or_base_url
            if model is None:
                raise TypeError("model is required when constructing LLMClient with base_url")
            self.config = LLMConfig(
                provider="openai" if base_url.startswith("http") else "unknown",
                api_base=base_url.rstrip("/"),
                model=model,
                api_key=api_key or "",
                base_url=base_url.rstrip("/"),
            )

        self.provider = self.config.provider
        self.model = self.config.model
        self.api_key = self.config.api_key
        self.base_url = self.config.base_url or self.config.api_base or _PROVIDER_BASE_URLS.get(self.provider, "")
        self._use_streaming = self.config.use_streaming
        litellm.suppress_debug_info = True

    def chat(
        self,
        messages: list[dict[str, Any]] | list[ChatMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_output_tokens: int | None = None,
        timeout: int = _TIMEOUT,
        num_retries: int = _MAX_RETRIES,
        drop_params: bool = True,
        **extra: Any,
    ) -> str:
        """Send a completion request and return plain text content."""

        effective_max_tokens = (
            max_output_tokens
            if max_output_tokens is not None
            else max_tokens
            if max_tokens is not None
            else _DEFAULT_MAX_TOKENS
        )
        payload_messages = [dict(message) for message in messages]

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": payload_messages,
            "max_tokens": effective_max_tokens,
            "timeout": timeout,
            "num_retries": num_retries,
            "drop_params": drop_params,
            "api_key": self.api_key or None,
            "api_base": self.config.api_base or None,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        kwargs.update(extra)

        try:
            if self._use_streaming:
                kwargs["stream"] = True
                response = litellm.completion(**kwargs)
                text = self._consume_stream(response)
            else:
                response = litellm.completion(**kwargs)
                text = self._extract_text(response)
        except Exception as exc:
            raise RuntimeError(f"LLM request failed ({self.provider}/{self.model}): {exc}") from exc

        if not text:
            raise RuntimeError("LLM response contained no text content.")
        return text

    def ask(self, prompt: str, **kwargs: Any) -> str:
        """Convenience helper for a single user message."""

        return self.chat([{"role": "user", "content": prompt}], **kwargs)

    def close(self) -> None:
        """LiteLLM completion() is stateless, so close() is a no-op."""

        return None

    @staticmethod
    def _extract_text(response: Any) -> str:
        choices = getattr(response, "choices", None)
        if not choices:
            return ""
        message = choices[0].message
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, str):
                    text_parts.append(part)
                elif isinstance(part, dict) and "text" in part:
                    text_parts.append(str(part["text"]))
            return "".join(text_parts).strip()
        return str(content).strip()

    @staticmethod
    def _consume_stream(response: Any) -> str:
        parts: list[str] = []
        for chunk in response:
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue
            content = getattr(delta, "content", None)
            if content:
                parts.append(content)
        return "".join(parts).strip()
_instance: LLMClient | None = None


def get_client() -> LLMClient:
    """Return the module-level client singleton."""

    global _instance
    if _instance is None:
        try:
            from applypilot.config import load_env

            load_env()
        except ModuleNotFoundError:
            log.debug("python-dotenv not installed; skipping .env auto-load in llm.get_client().")
        config = resolve_llm_config()
        log.info("LLM provider: %s  model: %s", config.provider, config.model)
        _instance = LLMClient(config)
    return _instance
