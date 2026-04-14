"""Tests for execution mode detection and headless/headful boundaries."""

import os
from applypilot.config.execution_mode import (
    ExecutionMode,
    detect_mode,
    is_headless,
    HEADLESS_SAFE,
    HEADFUL_REQUIRED,
)


def test_explicit_headless_env(monkeypatch):
    monkeypatch.setenv("APPLYPILOT_MODE", "headless")
    assert detect_mode() == ExecutionMode.HEADLESS
    assert is_headless()


def test_explicit_headful_env(monkeypatch):
    monkeypatch.setenv("APPLYPILOT_MODE", "headful")
    monkeypatch.setenv("TERM_PROGRAM", "iTerm2")
    assert detect_mode() == ExecutionMode.HEADFUL


def test_headless_safe_operations():
    assert "discover" in HEADLESS_SAFE
    assert "score" in HEADLESS_SAFE
    assert "tailor" in HEADLESS_SAFE
    assert "apply" not in HEADLESS_SAFE


def test_headful_required_operations():
    assert "apply" in HEADFUL_REQUIRED
    assert "discover" not in HEADFUL_REQUIRED


def test_render_from_db_fallback():
    """render_resume_from_db should not crash even without DB."""
    from applypilot.resume_render import render_resume_from_db

    # Will fail gracefully (no DB, no file) — just verify no crash
    try:
        render_resume_from_db()
    except (FileNotFoundError, SystemExit):
        pass  # Expected — no resume.json in test env
