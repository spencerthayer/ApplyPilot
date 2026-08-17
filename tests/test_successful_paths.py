"""Tests for the per-ATS successful-path memoization helper."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def tmp_paths_dir(tmp_path, monkeypatch):
    """Redirect PATHS_DIR to a tmp directory so tests don't pollute ~/.applypilot."""
    from applypilot.apply import successful_paths
    monkeypatch.setattr(successful_paths, "PATHS_DIR", tmp_path / "successful_paths")
    return tmp_path / "successful_paths"


def test_save_then_load_roundtrip(tmp_paths_dir):
    from applypilot.apply.successful_paths import save_path, load_path
    steps = [
        {"tool": "browser_navigate", "summary": "browser_navigate https://x"},
        {"tool": "browser_snapshot", "summary": "browser_snapshot"},
        {"tool": "browser_click",    "summary": "browser_click Apply"},
    ]
    out = save_path("greenhouse", steps, job_url="https://x", duration_ms=240_000)
    assert out is not None and out.exists()

    loaded = load_path("greenhouse")
    assert loaded is not None
    assert loaded["ats_slug"] == "greenhouse"
    assert loaded["job_url"] == "https://x"
    assert loaded["duration_ms"] == 240_000
    assert len(loaded["steps"]) == 3
    assert loaded["steps"][0]["tool"] == "browser_navigate"


def test_save_caps_to_max_steps(tmp_paths_dir):
    from applypilot.apply.successful_paths import save_path, load_path, MAX_STEPS
    steps = [{"tool": "x", "summary": f"step {i}"} for i in range(MAX_STEPS + 50)]
    save_path("workday", steps)
    loaded = load_path("workday")
    assert len(loaded["steps"]) == MAX_STEPS
    # Tail-preserved (most recent steps are the form-fill phase)
    assert loaded["steps"][-1]["summary"] == f"step {MAX_STEPS + 49}"


def test_save_empty_steps_is_noop(tmp_paths_dir):
    from applypilot.apply.successful_paths import save_path, load_path
    assert save_path("greenhouse", []) is None
    assert load_path("greenhouse") is None


def test_save_blank_slug_is_noop(tmp_paths_dir):
    from applypilot.apply.successful_paths import save_path
    assert save_path("", [{"tool": "x"}]) is None
    assert save_path(None, [{"tool": "x"}]) is None  # type: ignore[arg-type]


def test_load_missing_slug_returns_none(tmp_paths_dir):
    from applypilot.apply.successful_paths import load_path
    assert load_path("nonexistent") is None
    assert load_path("") is None


def test_format_for_prompt_renders_section(tmp_paths_dir):
    from applypilot.apply.successful_paths import save_path, load_path, format_path_for_prompt
    save_path("ashby", [
        {"tool": "browser_navigate", "summary": "browser_navigate https://ashby/x"},
        {"tool": "browser_click",    "summary": "browser_click Apply"},
    ], duration_ms=180_000)
    rendered = format_path_for_prompt(load_path("ashby"))
    assert rendered is not None
    assert "PRIOR SUCCESSFUL PATH (ashby)" in rendered
    assert "completed in 180s" in rendered
    assert "browser_click Apply" in rendered
    # Hint framing — explicit "guide, not a script"
    assert "guide" in rendered.lower()


def test_format_for_prompt_handles_none(tmp_paths_dir):
    from applypilot.apply.successful_paths import format_path_for_prompt
    assert format_path_for_prompt(None) is None
    assert format_path_for_prompt({}) is None
    assert format_path_for_prompt({"steps": []}) is None


def test_overwrite_replaces_prior(tmp_paths_dir):
    from applypilot.apply.successful_paths import save_path, load_path
    # No durations → newer wins (we treat duration-less memos as
    # always-overwrite for backward compat).
    save_path("lever", [{"tool": "old", "summary": "old run"}])
    save_path("lever", [{"tool": "new", "summary": "new run"}])
    loaded = load_path("lever")
    assert len(loaded["steps"]) == 1
    assert loaded["steps"][0]["summary"] == "new run"


def test_keep_fastest_when_durations_present(tmp_paths_dir):
    """A slower run should NOT overwrite an existing faster memo."""
    from applypilot.apply.successful_paths import save_path, load_path
    save_path("greenhouse",
              [{"tool": "fast", "summary": "fast path step"}],
              duration_ms=240_000)
    # Slower run — should be skipped
    result = save_path("greenhouse",
                       [{"tool": "slow", "summary": "slow path step"}],
                       duration_ms=857_000)
    assert result is None, "save should return None when skipped"
    loaded = load_path("greenhouse")
    assert loaded["duration_ms"] == 240_000
    assert loaded["steps"][0]["summary"] == "fast path step"


def test_faster_run_wins_over_existing(tmp_paths_dir):
    """A faster run should replace a slower memo."""
    from applypilot.apply.successful_paths import save_path, load_path
    save_path("greenhouse",
              [{"tool": "slow", "summary": "first slow run"}],
              duration_ms=857_000)
    result = save_path("greenhouse",
                       [{"tool": "fast", "summary": "second fast run"}],
                       duration_ms=240_000)
    assert result is not None
    loaded = load_path("greenhouse")
    assert loaded["duration_ms"] == 240_000
    assert loaded["steps"][0]["summary"] == "second fast run"


def test_first_save_always_writes(tmp_paths_dir):
    """First memo for a slug writes regardless of how slow it is."""
    from applypilot.apply.successful_paths import save_path, load_path
    out = save_path("ashby",
                    [{"tool": "x", "summary": "first time on ashby"}],
                    duration_ms=999_000)
    assert out is not None
    assert load_path("ashby") is not None


def _write_legacy_memo(paths_dir, slug, steps):
    """Hand-write a pre-keep-fastest memo file: no duration_ms key at all."""
    import json as _json
    from datetime import datetime, timezone
    paths_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "ats_slug": slug,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "job_url": "https://legacy.example/job",
        "steps": steps,
    }
    (paths_dir / f"{slug}.json").write_text(_json.dumps(payload))


def test_legacy_file_without_duration_gets_replaced(tmp_paths_dir):
    """A stored file with no duration field counts as infinitely slow —
    the first timed run replaces it, regardless of how slow it was."""
    from applypilot.apply.successful_paths import save_path, load_path
    _write_legacy_memo(tmp_paths_dir, "icims",
                       [{"tool": "old", "summary": "legacy run"}])
    # New timed run — even a very slow one — must win over legacy.
    result = save_path("icims",
                       [{"tool": "new", "summary": "first timed run"}],
                       duration_ms=999_000)
    assert result is not None
    loaded = load_path("icims")
    assert loaded["duration_ms"] == 999_000
    assert loaded["steps"][0]["summary"] == "first timed run"


def test_load_legacy_format(tmp_paths_dir):
    """load_path + format_path_for_prompt work on files lacking duration_ms."""
    from applypilot.apply.successful_paths import load_path, format_path_for_prompt
    _write_legacy_memo(tmp_paths_dir, "workday",
                       [{"tool": "browser_click", "summary": "browser_click Apply"}])
    loaded = load_path("workday")
    assert loaded is not None
    assert "duration_ms" not in loaded
    assert loaded["steps"][0]["summary"] == "browser_click Apply"
    rendered = format_path_for_prompt(loaded)
    assert rendered is not None
    assert "PRIOR SUCCESSFUL PATH (workday)" in rendered
    assert "completed in 0s" in rendered  # missing duration renders as 0
    assert "browser_click Apply" in rendered


# ── Age-based eviction ──────────────────────────────────────────────────────

def test_load_skips_stale_memo(tmp_paths_dir):
    """A memo older than MAX_AGE_DAYS should not be returned to callers."""
    import json as _json
    from datetime import datetime, timezone, timedelta
    from applypilot.apply.successful_paths import (
        save_path, load_path, MAX_AGE_DAYS, PATHS_DIR,
    )
    # Save a memo with a 99-days-ago captured_at by hand-editing the file
    save_path("greenhouse", [{"tool": "x", "summary": "step"}],
              duration_ms=240_000)
    out_path = PATHS_DIR / "greenhouse.json"
    payload = _json.loads(out_path.read_text())
    payload["captured_at"] = (
        datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS + 5)
    ).isoformat()
    out_path.write_text(_json.dumps(payload))

    assert load_path("greenhouse") is None, "stale memo should be hidden"


def test_load_returns_fresh_memo(tmp_paths_dir):
    """A memo younger than MAX_AGE_DAYS is returned normally."""
    from applypilot.apply.successful_paths import save_path, load_path
    save_path("greenhouse", [{"tool": "x", "summary": "step"}],
              duration_ms=240_000)
    loaded = load_path("greenhouse")
    assert loaded is not None
    assert loaded["ats_slug"] == "greenhouse"


def test_stale_memo_overwritten_by_new_save(tmp_paths_dir):
    """A stale memo is invisible to the keep-fastest check, so any new
    save (even a slow one) replaces it."""
    import json as _json
    from datetime import datetime, timezone, timedelta
    from applypilot.apply.successful_paths import (
        save_path, load_path, MAX_AGE_DAYS, PATHS_DIR,
    )
    # Plant a "fast but ancient" memo at 50 days old
    save_path("greenhouse", [{"tool": "fast", "summary": "fast ancient"}],
              duration_ms=100_000)
    out_path = PATHS_DIR / "greenhouse.json"
    payload = _json.loads(out_path.read_text())
    payload["captured_at"] = (
        datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS + 20)
    ).isoformat()
    out_path.write_text(_json.dumps(payload))

    # A newer SLOW save should still win because the ancient one is stale
    result = save_path("greenhouse",
                       [{"tool": "slow", "summary": "slow but recent"}],
                       duration_ms=900_000)
    assert result is not None
    loaded = load_path("greenhouse")
    assert loaded["steps"][0]["summary"] == "slow but recent"
    assert loaded["duration_ms"] == 900_000
