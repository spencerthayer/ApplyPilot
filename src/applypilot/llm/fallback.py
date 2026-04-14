"""LLM fallback chain builder (LLD §12.3).

Builds per-tier model preference lists from configured provider keys.
"""

from __future__ import annotations

import os

from applypilot.llm.config import LLMTier, ModelEntry, raw_model_name

_PROVIDER_BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
}

_TIER_MODELS: dict[LLMTier, dict[str, list[str]]] = {
    LLMTier.CHEAP: {
        "gemini": ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"],
        "openai": ["gpt-4.1-nano", "gpt-4.1-mini"],
        "anthropic": ["claude-haiku-4-5-20251001"],
        "deepseek": ["deepseek-chat"],
    },
    LLMTier.MID: {
        "gemini": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"],
        "openai": ["gpt-4.1-mini", "gpt-4.1-nano"],
        "anthropic": ["claude-sonnet-4-5-20250514", "claude-haiku-4-5-20251001"],
        "deepseek": ["deepseek-chat"],
    },
    LLMTier.PREMIUM: {
        "gemini": ["gemini-2.5-pro", "gemini-2.5-flash"],
        "openai": ["gpt-4.1", "gpt-4.1-mini"],
        "anthropic": ["claude-sonnet-4-5-20250514"],
        "deepseek": [],
    },
}


def build_fallback_chain(primary_model: str, tier: LLMTier = LLMTier.CHEAP) -> list[ModelEntry]:
    """Build a best-effort fallback chain using configured provider keys."""
    primary_name = raw_model_name(primary_model)
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()

    models_by_provider = _TIER_MODELS[tier]
    chain: list[ModelEntry] = []

    def _append(models: list[str], provider: str, api_key: str, base_url: str) -> None:
        for model_name in models:
            chain.append(ModelEntry(model_name, provider, base_url, api_key))

    if gemini_key:
        gemini_models = models_by_provider["gemini"]
        if primary_name in gemini_models:
            start = gemini_models.index(primary_name)
            _append(gemini_models[start:], "gemini", gemini_key, _PROVIDER_BASE_URLS["gemini"])
            _append(gemini_models[:start], "gemini", gemini_key, _PROVIDER_BASE_URLS["gemini"])
        else:
            chain.append(ModelEntry(primary_name, "gemini", _PROVIDER_BASE_URLS["gemini"], gemini_key))
            _append(
                [n for n in gemini_models if n != primary_name], "gemini", gemini_key, _PROVIDER_BASE_URLS["gemini"]
            )

    if openai_key:
        _append(models_by_provider["openai"], "openai", openai_key, _PROVIDER_BASE_URLS["openai"])

    if deepseek_key and (ds := models_by_provider["deepseek"]):
        _append(ds, "deepseek", deepseek_key, "https://api.deepseek.com/v1")

    if anthropic_key:
        _append(models_by_provider["anthropic"], "anthropic", anthropic_key, _PROVIDER_BASE_URLS["anthropic"])

    if not chain:
        raise RuntimeError(
            "No LLM provider configured. Set GEMINI_API_KEY, OPENAI_API_KEY, DEEPSEEK_API_KEY, or ANTHROPIC_API_KEY."
        )

    deduped: list[ModelEntry] = []
    seen: set[tuple[str, str]] = set()
    for entry in chain:
        key = (entry.provider, entry.name)
        if key not in seen:
            seen.add(key)
            deduped.append(entry)
    return deduped
