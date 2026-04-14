"""Test that native agent imports resolve correctly."""


def test_native_agent_log_imports():
    """Regression: _job_log_path was renamed to job_log_path."""
    from applypilot.apply.backends import job_log_path, log_header

    assert callable(job_log_path)
    assert callable(log_header)


def test_native_agent_prompt_has_cookie_step():
    """Cookie consent dismissal must be in the native agent workflow."""
    from applypilot.apply.native_agent import _SYSTEM_PROMPT

    assert "cookie" in _SYSTEM_PROMPT.lower() or "accept" in _SYSTEM_PROMPT.lower()
