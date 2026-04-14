from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from applypilot import config
from applypilot.llm_provider import (
    detect_llm_provider,
    format_llm_provider_status,
)
from applypilot.wizard.init import _build_ai_env_lines


LLM_ENV_KEYS = (
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "BEDROCK_MODEL_ID",
    "BEDROCK_REGION",
    "LLM_URL",
    "LLM_MODEL",
    "LLM_MODEL_MID",
    "LLM_MODEL_PREMIUM",
    "LLM_MODEL_QUALITY",
    "LLM_API_KEY",
)


def _clear_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in LLM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _missing_chrome() -> str:
    raise FileNotFoundError("missing chrome")


def test_detect_llm_provider_uses_openrouter_defaults() -> None:
    selection = detect_llm_provider({"OPENROUTER_API_KEY": "or-key"})

    assert selection is not None
    assert selection.spec.key == "openrouter"
    assert selection.base_url == "https://openrouter.ai/api/v1"
    assert selection.model == "google/gemini-2.0-flash-001"
    assert selection.api_key == "or-key"


def test_detect_llm_provider_respects_model_override() -> None:
    selection = detect_llm_provider(
        {
            "OPENROUTER_API_KEY": "or-key",
            "LLM_MODEL": "anthropic/claude-3.5-haiku",
        }
    )

    assert selection is not None
    assert selection.model == "anthropic/claude-3.5-haiku"


def test_detect_llm_provider_precedence_prefers_local_then_gemini_then_openrouter() -> None:
    selection = detect_llm_provider(
        {
            "LLM_URL": "http://localhost:8080/v1/",
            "LLM_API_KEY": "local-key",
            "GEMINI_API_KEY": "gemini-key",
            "OPENROUTER_API_KEY": "or-key",
            "OPENAI_API_KEY": "openai-key",
        }
    )
    assert selection is not None
    assert selection.spec.key == "local"
    assert selection.base_url == "http://localhost:8080/v1"

    selection = detect_llm_provider(
        {
            "GEMINI_API_KEY": "gemini-key",
            "OPENROUTER_API_KEY": "or-key",
            "OPENAI_API_KEY": "openai-key",
        }
    )
    assert selection is not None
    assert selection.spec.key == "gemini"

    selection = detect_llm_provider(
        {
            "OPENROUTER_API_KEY": "or-key",
            "OPENAI_API_KEY": "openai-key",
        }
    )
    assert selection is not None
    assert selection.spec.key == "openrouter"


def test_format_llm_provider_status_uses_openrouter_label() -> None:
    status = format_llm_provider_status({"OPENROUTER_API_KEY": "or-key"})

    assert status == "OpenRouter (google/gemini-2.0-flash-001)"


def test_build_ai_env_lines_writes_openrouter_key() -> None:
    lines = _build_ai_env_lines(
        "openrouter",
        "or-key",
        "google/gemini-2.0-flash-001",
    )

    assert lines == [
        "# ApplyPilot configuration",
        "",
        "OPENROUTER_API_KEY=or-key",
        "LLM_MODEL=google/gemini-2.0-flash-001",
        "",
    ]


def test_get_tier_counts_openrouter_as_tier_two(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.setattr(config, "get_chrome_path", _missing_chrome)
    monkeypatch.setattr(config.shutil, "which", lambda _: None)
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")

    assert config.get_tier() == 2


def test_check_tier_missing_message_mentions_all_supported_llm_envs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(config, "load_env", lambda: None)
    _clear_llm_env(monkeypatch)

    with pytest.raises(SystemExit):
        config.check_tier(2, "AI scoring")

    captured = capsys.readouterr()
    combined = f"{captured.out}\n{captured.err}"
    for snippet in (
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "LLM_URL",
        "applypilot init",
    ):
        assert snippet in combined
    assert "OPENROUTER_API_KEY" in combined
    assert "LLM_URL" in combined


def test_pyproject_lists_tenacity_runtime_dependency() -> None:
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    dependencies = data["project"]["dependencies"]
    assert any(dep.startswith("tenacity") for dep in dependencies)
