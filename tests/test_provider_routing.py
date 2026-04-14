"""Tests for LLM provider routing precedence (router-first).

@file test_provider_routing.py
@description Validates that _detect_provider() and get_client() honour the
             router-first priority: LLM_URL > GEMINI_API_KEY > OPENAI_API_KEY.
             Also verifies the no-provider RuntimeError path.
"""

from __future__ import annotations

import pytest

from applypilot import llm
from applypilot.llm import _detect_provider, get_client, LLMClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Keys used only inside env-patching; never sent over the wire.
_FAKE_GEMINI = "fake-gemini-key-for-test"
_FAKE_OPENAI = "fake-openai-key-for-test"
_FAKE_LLM_URL = "http://my-9router.test/v1"
_FAKE_LLM_KEY = "fake-gateway-key-for-test"


def _clear_llm_env(monkeypatch):
    """Remove all LLM-related env vars so each test starts clean."""
    for var in (
            "GEMINI_API_KEY",
            "OPENAI_API_KEY",
            "OPENROUTER_API_KEY",
            "ANTHROPIC_API_KEY",
            "DEEPSEEK_API_KEY",
            "LLM_URL",
            "LLM_API_KEY",
            "LLM_MODEL",
            "LLM_MODEL_QUALITY",
            "BEDROCK_MODEL_ID",
            "BEDROCK_REGION",
    ):
        monkeypatch.delenv(var, raising=False)
    # Prevent get_client() from reloading .env mid-test
    monkeypatch.setattr("applypilot.config.load_env", lambda: None)


def _reset_singleton(monkeypatch):
    """Reset the per-tier singleton cache so get_client() re-detects."""
    monkeypatch.setattr(llm, "_tier_instances", {})


# ---------------------------------------------------------------------------
# 1. LLM_URL takes precedence over all API keys
# ---------------------------------------------------------------------------


class TestLLMURLPrecedence:
    """LLM_URL must win even when Gemini and/or OpenAI keys are present."""

    def test_llm_url_beats_gemini_key(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("GEMINI_API_KEY", _FAKE_GEMINI)
        monkeypatch.setenv("LLM_URL", _FAKE_LLM_URL)
        monkeypatch.setenv("LLM_API_KEY", _FAKE_LLM_KEY)

        base_url, model, api_key = _detect_provider()

        assert base_url == _FAKE_LLM_URL.rstrip("/")
        assert api_key == _FAKE_LLM_KEY
        assert "gemini" not in base_url.lower()

    def test_llm_url_beats_openai_key(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", _FAKE_OPENAI)
        monkeypatch.setenv("LLM_URL", _FAKE_LLM_URL)
        monkeypatch.setenv("LLM_API_KEY", _FAKE_LLM_KEY)

        base_url, model, api_key = _detect_provider()

        assert base_url == _FAKE_LLM_URL.rstrip("/")
        assert api_key == _FAKE_LLM_KEY
        assert "openai" not in base_url.lower()

    def test_llm_url_beats_both_keys(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("GEMINI_API_KEY", _FAKE_GEMINI)
        monkeypatch.setenv("OPENAI_API_KEY", _FAKE_OPENAI)
        monkeypatch.setenv("LLM_URL", _FAKE_LLM_URL)
        monkeypatch.setenv("LLM_API_KEY", _FAKE_LLM_KEY)

        base_url, model, api_key = _detect_provider()

        assert base_url == _FAKE_LLM_URL.rstrip("/")
        assert api_key == _FAKE_LLM_KEY

    def test_llm_url_strips_trailing_slash(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_URL", _FAKE_LLM_URL + "/")

        base_url, _, _ = _detect_provider()

        assert not base_url.endswith("/")

    def test_llm_url_default_model(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_URL", _FAKE_LLM_URL)

        _, model, _ = _detect_provider()

        assert model == "local-model"

    def test_llm_url_model_override(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_URL", _FAKE_LLM_URL)
        monkeypatch.setenv("LLM_MODEL", "my-custom-model")

        _, model, _ = _detect_provider()

        assert model == "my-custom-model"

    def test_llm_url_empty_api_key_when_unset(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_URL", _FAKE_LLM_URL)

        _, _, api_key = _detect_provider()

        assert api_key == ""


# ---------------------------------------------------------------------------
# 2. Gemini fallback when LLM_URL is absent
# ---------------------------------------------------------------------------


class TestGeminiFallback:
    """GEMINI_API_KEY should be selected when LLM_URL is unset."""

    def test_gemini_selected_when_only_gemini_key(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("GEMINI_API_KEY", _FAKE_GEMINI)

        base_url, model, api_key = _detect_provider()

        assert "generativelanguage.googleapis.com" in base_url
        assert model == "gemini-2.0-flash"
        assert api_key == _FAKE_GEMINI

    def test_gemini_beats_openai_when_both_present(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("GEMINI_API_KEY", _FAKE_GEMINI)
        monkeypatch.setenv("OPENAI_API_KEY", _FAKE_OPENAI)

        base_url, _, api_key = _detect_provider()

        assert "generativelanguage.googleapis.com" in base_url
        assert api_key == _FAKE_GEMINI

    def test_gemini_model_override(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("GEMINI_API_KEY", _FAKE_GEMINI)
        monkeypatch.setenv("LLM_MODEL", "gemini-2.5-pro")

        _, model, _ = _detect_provider()

        assert model == "gemini-2.5-pro"


# ---------------------------------------------------------------------------
# 3. OpenAI fallback when LLM_URL and GEMINI_API_KEY are absent
# ---------------------------------------------------------------------------


class TestOpenAIFallback:
    """OPENAI_API_KEY should be selected only when both LLM_URL and GEMINI are unset."""

    def test_openai_selected_when_only_openai_key(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", _FAKE_OPENAI)

        base_url, model, api_key = _detect_provider()

        assert base_url == "https://api.openai.com/v1"
        assert model == "gpt-4o-mini"
        assert api_key == _FAKE_OPENAI

    def test_openai_model_override(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", _FAKE_OPENAI)
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        _, model, _ = _detect_provider()

        assert model == "gpt-4o"


# ---------------------------------------------------------------------------
# 4. No provider → RuntimeError
# ---------------------------------------------------------------------------


class TestNoProvider:
    """When no env vars are set, _detect_provider must raise RuntimeError."""

    def test_no_keys_raises_runtime_error(self, monkeypatch):
        _clear_llm_env(monkeypatch)

        with pytest.raises(RuntimeError, match="No LLM provider configured"):
            _detect_provider()

    def test_error_message_lists_all_options(self, monkeypatch):
        _clear_llm_env(monkeypatch)

        with pytest.raises(RuntimeError) as exc_info:
            _detect_provider()

        msg = str(exc_info.value)
        assert "LLM_URL" in msg
        assert "GEMINI_API_KEY" in msg
        assert "OPENAI_API_KEY" in msg


# ---------------------------------------------------------------------------
# 5. get_client() integration (singleton resets)
# ---------------------------------------------------------------------------
