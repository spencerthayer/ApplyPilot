"""Unified LLM client for ApplyPilot using LiteLLM internally.

Public contract:
  - Provider detection stays anchored in applypilot.llm_provider.
  - OpenRouter remains a first-class user-facing provider.
  - LLMClient.chat() accepts both max_tokens and max_output_tokens.
"""

from __future__ import annotations

import atexit
import logging
import os
import threading
import time
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
_EXHAUSTION_COOLDOWN_SECONDS = 300
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


def _raw_model_name(model: str) -> str:
    _, sep, remainder = model.partition("/")
    return remainder if sep else model


def _build_fallback_chain(primary_model: str, quality: bool = False) -> list[ModelEntry]:
    """Build a best-effort fallback chain using configured provider keys."""

    primary_name = _raw_model_name(primary_model)
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()

    gemini_models = (
        ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"]
        if quality
        else ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"]
    )
    openai_models = ["gpt-4.1-mini", "gpt-4.1-nano"] if quality else ["gpt-4.1-nano", "gpt-4.1-mini"]
    anthropic_models = (
        ["claude-sonnet-4-5-20250514", "claude-haiku-4-5-20251001"]
        if quality
        else ["claude-haiku-4-5-20251001"]
    )
    deepseek_models = ["deepseek-chat"]

    chain: list[ModelEntry] = []

    def _append(models: list[str], provider: str, api_key: str, base_url: str) -> None:
        for model_name in models:
            chain.append(ModelEntry(model_name, provider, base_url, api_key))

    if gemini_key:
        if primary_name in gemini_models:
            start = gemini_models.index(primary_name)
            _append(gemini_models[start:], "gemini", gemini_key, _PROVIDER_BASE_URLS["gemini"])
            _append(gemini_models[:start], "gemini", gemini_key, _PROVIDER_BASE_URLS["gemini"])
        else:
            chain.append(ModelEntry(primary_name, "gemini", _PROVIDER_BASE_URLS["gemini"], gemini_key))
            _append([name for name in gemini_models if name != primary_name], "gemini", gemini_key, _PROVIDER_BASE_URLS["gemini"])

    if openai_key:
        _append(openai_models, "openai", openai_key, _PROVIDER_BASE_URLS["openai"])

    if deepseek_key:
        _append(deepseek_models, "deepseek", deepseek_key, "https://api.deepseek.com/v1")

    if anthropic_key:
        _append(anthropic_models, "anthropic", anthropic_key, _PROVIDER_BASE_URLS["anthropic"])

    if not chain:
        raise RuntimeError(
            "No LLM provider configured. "
            "Set GEMINI_API_KEY, OPENAI_API_KEY, DEEPSEEK_API_KEY, or ANTHROPIC_API_KEY."
        )

    deduped: list[ModelEntry] = []
    seen: set[tuple[str, str]] = set()
    for entry in chain:
        key = (entry.provider, entry.name)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def _detect_provider() -> tuple[str, str, str]:
    """Return the canonical provider tuple expected by main-branch callers."""

    selection = detect_llm_provider()
    if selection is not None:
        return selection.base_url, selection.model, selection.api_key
    raise RuntimeError(f"No LLM provider configured. {llm_config_hint()}")


def resolve_llm_config(env: Mapping[str, str] | None = None, quality: bool = False) -> LLMConfig:
    """Resolve runtime LLM configuration while preserving main's provider contract."""

    env_map = os.environ if env is None else env
    selection = detect_llm_provider(env_map)
    configured_model = _env_get(env_map, "LLM_MODEL_QUALITY") if quality else ""
    if not configured_model:
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

    def __init__(
        self,
        config_or_base_url: LLMConfig | str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        quality: bool = False,
        *,
        base_url: str | None = None,
    ) -> None:
        config_source = base_url if base_url is not None else config_or_base_url
        if isinstance(config_source, LLMConfig):
            self.config = config_source
        else:
            if config_source is None:
                raise TypeError("base_url or LLMConfig is required when constructing LLMClient")
            resolved_base_url = str(config_source)
            if model is None:
                raise TypeError("model is required when constructing LLMClient with base_url")
            self.config = LLMConfig(
                provider="openai" if resolved_base_url.startswith("http") else "unknown",
                api_base=resolved_base_url.rstrip("/"),
                model=model,
                api_key=api_key or "",
                base_url=resolved_base_url.rstrip("/"),
            )

        self.provider = self.config.provider
        self.model = self.config.model
        self.api_key = self.config.api_key
        self.base_url = self.config.base_url or self.config.api_base or _PROVIDER_BASE_URLS.get(self.provider, "")
        self._use_streaming = self.config.use_streaming
        self.quality = quality
        self._request_options: dict[str, Any] = {}
        self._exhausted: dict[str, float] = {}
        litellm.suppress_debug_info = True

        self._fallback_chain = [self._primary_entry()]
        try:
            for entry in _build_fallback_chain(_raw_model_name(self.model), quality=self.quality):
                if any(existing.name == entry.name for existing in self._fallback_chain):
                    continue
                self._fallback_chain.append(entry)
        except RuntimeError:
            pass

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

        self._request_options = {
            "timeout": timeout,
            "num_retries": num_retries,
            "drop_params": drop_params,
            "extra": dict(extra),
        }
        try:
            entries_to_try = self._active_entries()
            for index, entry in enumerate(entries_to_try):
                result = self._try_entry(
                    entry,
                    payload_messages,
                    temperature,
                    effective_max_tokens,
                    index == len(entries_to_try) - 1,
                )
                if result is not None:
                    return result
        finally:
            self._request_options = {}

        raise RuntimeError(
            "All configured LLM models are temporarily exhausted. "
            "Wait a few minutes for rate limits to reset."
        )

    def ask(self, prompt: str, **kwargs: Any) -> str:
        """Convenience helper for a single user message."""

        return self.chat([{"role": "user", "content": prompt}], **kwargs)

    def close(self) -> None:
        """LiteLLM completion() is stateless, so close() is a no-op."""

        return None

    def _primary_entry(self) -> ModelEntry:
        return ModelEntry(
            name=_raw_model_name(self.model),
            provider=self.provider,
            base_url=self.config.api_base or "",
            api_key=self.api_key,
        )

    def _active_entries(self) -> list[ModelEntry]:
        now = time.time()
        active = [
            entry
            for entry in self._fallback_chain
            if entry.name not in self._exhausted
            or (now - self._exhausted[entry.name]) > _EXHAUSTION_COOLDOWN_SECONDS
        ]
        if active:
            return active
        self._exhausted.clear()
        return list(self._fallback_chain)

    def _entry_model(self, entry: ModelEntry) -> str:
        if entry.provider == "unknown":
            return self.model
        if "/" in entry.name:
            return entry.name
        return _normalize_model(entry.provider, entry.name)

    def _try_entry(
        self,
        entry: ModelEntry,
        messages: list[dict[str, Any]] | list[ChatMessage],
        temperature: float | None,
        max_tokens: int,
        is_last: bool,
    ) -> str | None:
        options = self._request_options
        kwargs: dict[str, Any] = {
            "model": self._entry_model(entry),
            "messages": [dict(message) for message in messages],
            "max_tokens": max_tokens,
            "timeout": options.get("timeout", _TIMEOUT),
            "num_retries": options.get("num_retries", _MAX_RETRIES),
            "drop_params": options.get("drop_params", True),
            "api_key": entry.api_key or None,
            "api_base": entry.base_url or None,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        kwargs.update(options.get("extra", {}))

        try:
            if self._use_streaming:
                kwargs["stream"] = True
                response = litellm.completion(**kwargs)
                text = self._consume_stream(response)
            else:
                response = litellm.completion(**kwargs)
                text = self._extract_text(response)
        except Exception as exc:
            message = str(exc).lower()
            if any(token in message for token in ("429", "rate limit", "quota", "resource has been exhausted", "payment required")):
                self._exhausted[entry.name] = time.time()
                if not is_last:
                    return None
            raise RuntimeError(f"LLM request failed ({entry.provider}/{entry.name}): {exc}") from exc

        if not text:
            if not is_last:
                return None
            raise RuntimeError("LLM response contained no text content.")
        return text

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
_quality_instance: LLMClient | None = None
_instance_lock = threading.Lock()


def get_client(quality: bool = False) -> LLMClient:
    """Return the module-level client singleton."""

    global _instance, _quality_instance
    target = _quality_instance if quality else _instance
    if target is None:
        with _instance_lock:
            target = _quality_instance if quality else _instance
            if target is None:
                try:
                    from applypilot.config import load_env

                    load_env()
                except ModuleNotFoundError:
                    log.debug("python-dotenv not installed; skipping .env auto-load in llm.get_client().")
                config = resolve_llm_config(quality=True) if quality else resolve_llm_config()
                log.info("LLM provider: %s  model: %s", config.provider, config.model)
                target = LLMClient(config, quality=quality) if quality else LLMClient(config)
                atexit.register(target.close)
                if quality:
                    _quality_instance = target
                else:
                    _instance = target
    return target
