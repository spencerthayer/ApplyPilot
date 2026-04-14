"""LLM client — unified chat interface."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
import warnings
from types import SimpleNamespace
from typing import Any

try:
    import litellm
except ModuleNotFoundError:

    def _missing_litellm(**_: Any) -> Any:
        raise RuntimeError("litellm is required for ApplyPilot LLM requests.")


    litellm = SimpleNamespace(completion=_missing_litellm, suppress_debug_info=False)

from applypilot.llm_provider import detect_llm_provider, llm_config_hint

# Re-export from decomposed submodules
from applypilot.llm.config import (  # noqa: F401
    LLMTier,
    LLMConfig,
    ModelEntry,
    ChatMessage,
    LiteLLMExtra,
    resolve_llm_config,
    normalize_model as _normalize_model,
    provider_from_model as _provider_from_model,
    raw_model_name as _raw_model_name,
    is_provider_qualified as _is_provider_qualified_model,
    _env_get,
)
from applypilot.llm.fallback import build_fallback_chain as _build_fallback_chain  # noqa: F401
from applypilot.llm.rate_limiter import (  # noqa: F401
    is_openrouter_free_model as _is_openrouter_free_model,
    apply_openrouter_pacing as _apply_openrouter_pacing,
    respect_openrouter_cooldown as _respect_openrouter_cooldown,
    note_openrouter_rate_limit as _note_openrouter_rate_limit,
)
from applypilot.llm.cost_tracker import CostTracker  # noqa: F401

warnings.filterwarnings("ignore", category=UserWarning, module="pydantic.*")

log = logging.getLogger(__name__)

_MAX_RETRIES = 5
_TIMEOUT = 120
_DEFAULT_MAX_TOKENS = 4096
_EXHAUSTION_COOLDOWN_SECONDS = 300
_STREAMING_TRUE_VALUES = {"1", "true", "yes"}
_OPENROUTER_FREE_MIN_INTERVAL_SECONDS = 3.5
_OPENROUTER_RATE_LIMIT_COOLDOWN_SECONDS = 20.0
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


def _detect_provider() -> tuple[str, str, str]:
    """Return the canonical provider tuple expected by main-branch callers."""

    selection = detect_llm_provider()
    if selection is not None:
        return selection.base_url, selection.model, selection.api_key
    raise RuntimeError(f"No LLM provider configured. {llm_config_hint()}")


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
        self._cost_tracker = CostTracker()
        self._request_options: dict[str, Any] = {}
        self._exhausted: dict[str, float] = {}
        litellm.suppress_debug_info = True

        self._fallback_chain = [self._primary_entry()]
        try:
            tier = LLMTier.QUALITY if self.quality else LLMTier.CHEAP
            for entry in _build_fallback_chain(_raw_model_name(self.model), tier=tier):
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
            "All configured LLM models are temporarily exhausted. Wait a few minutes for rate limits to reset."
        )

    def ask(self, prompt: str, **kwargs: Any) -> str:
        """Convenience helper for a single user message."""

        return self.chat([{"role": "user", "content": prompt}], **kwargs)

    def close(self) -> None:
        """LiteLLM completion() is stateless, so close() is a no-op."""

        return None

    def _primary_entry(self) -> ModelEntry:
        # OpenRouter model IDs often include nested slashes (vendor/model), so
        # keep the provider-qualified model string for the primary entry.
        primary_name = self.model if self.provider == "openrouter" else _raw_model_name(self.model)
        return ModelEntry(
            name=primary_name,
            provider=self.provider,
            base_url=self.config.base_url or self.config.api_base or "",
            api_key=self.api_key,
        )

    def _active_entries(self) -> list[ModelEntry]:
        now = time.time()
        active = [
            entry
            for entry in self._fallback_chain
            if entry.name not in self._exhausted or (now - self._exhausted[entry.name]) > _EXHAUSTION_COOLDOWN_SECONDS
        ]
        if active:
            return active
        self._exhausted.clear()
        return list(self._fallback_chain)

    def _entry_model(self, entry: ModelEntry) -> str:
        if entry.provider == "unknown":
            return self.model
        if _is_provider_qualified_model(entry.name):
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
            "base_url": entry.base_url or None,
        }
        if entry.provider == "bedrock":
            kwargs.pop("api_key", None)
            kwargs.pop("api_base", None)
            region = os.environ.get("BEDROCK_REGION", "us-east-1").strip()
            kwargs["aws_region_name"] = region
        if temperature is not None:
            kwargs["temperature"] = temperature
        kwargs.update(options.get("extra", {}))

        try:
            model_name = self._entry_model(entry)
            _respect_openrouter_cooldown(model_name)
            _apply_openrouter_pacing(model_name)
            t0 = time.time()
            if self._use_streaming:
                kwargs["stream"] = True
                response = litellm.completion(**kwargs)
                text = self._consume_stream(response)
            else:
                response = litellm.completion(**kwargs)
                text = self._extract_text(response)
            elapsed = time.time() - t0

            # Log request metrics — helps debug latency, cost, and token usage
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
            output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
            log.debug(
                "[llm] %s | %.1fs | in=%d out=%d tokens | model=%s",
                entry.provider,
                elapsed,
                input_tokens,
                output_tokens,
                model_name,
            )

            # Track cost (in-memory for this session)
            cost = getattr(getattr(response, "_hidden_params", None), "response_cost", 0.0) or 0.0
            self._cost_tracker.record(entry.provider, model_name, input_tokens, output_tokens, cost)

            # Persist to DB for cross-session reporting (if cost_tracking enabled)
            try:
                from applypilot.bootstrap import get_app
                import json as _json
                from applypilot.db.dto import AnalyticsEventDTO
                from uuid import uuid4

                app = get_app()
                if app.config.pipeline.cost_tracking:
                    app.container.analytics_repo.emit_event(
                        AnalyticsEventDTO(
                            event_id=str(uuid4()),
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            stage="llm",
                            event_type="llm_call",
                            payload=_json.dumps(
                                {
                                    "provider": entry.provider,
                                    "model": model_name,
                                    "tokens_in": input_tokens,
                                    "tokens_out": output_tokens,
                                    "cost_usd": cost,
                                    "elapsed_s": round(elapsed, 2),
                                }
                            ),
                        )
                    )
            except Exception:
                pass  # Don't let cost logging break LLM calls
        except Exception as exc:
            message = str(exc).lower()
            if any(
                    token in message
                    for token in (
                            "429",
                            "rate limit",
                            "quota",
                            "resource has been exhausted",
                            "payment required",
                            "throttlingexception",
                    )
            ):
                _note_openrouter_rate_limit(self._entry_model(entry))
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
        content = getattr(message, "content", None)
        text = LLMClient._coerce_text(content)
        if text:
            return text
        reasoning_content = getattr(message, "reasoning_content", None)
        text = LLMClient._coerce_text(reasoning_content)
        if text:
            return text
        return ""

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
            text = LLMClient._coerce_text(content)
            if text:
                parts.append(text)
            reasoning_content = getattr(delta, "reasoning_content", None)
            text = LLMClient._coerce_text(reasoning_content)
            if text:
                parts.append(text)
        return "".join(parts).strip()

    @staticmethod
    def _coerce_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            text_parts: list[str] = []
            for part in value:
                if isinstance(part, str):
                    text_parts.append(part)
                elif isinstance(part, dict):
                    text = part.get("text")
                    if text is not None:
                        text_parts.append(str(text))
            return "".join(text_parts).strip()
        return str(value).strip()
