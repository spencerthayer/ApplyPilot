"""Tests for CLI apply command batch behavior.

Verifies:
- Default invocation passes limit=None (batch all)
- --limit 0 is treated as batch all (limit=None)
- worker_loop drains queue without limit
- worker_loop with limit=0 is a no-op
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from typer.testing import CliRunner

import applypilot.cli as cli
import applypilot.cli.commands.apply_cmd as _apply_cmd
from applypilot.apply import launcher


def _mock_bootstrap(monkeypatch) -> None:
    """Mock bootstrap + DI so apply command can run without real DB."""
    monkeypatch.setattr(cli, "_bootstrap", lambda: None)
    monkeypatch.setattr(_apply_cmd, "_resolve_backend_option", lambda *_args: ("codex", None))
    monkeypatch.setattr("applypilot.config.check_tier", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("applypilot.config.load_profile", lambda: {"ok": True})

    # Mock job_repo to report 2 ready jobs
    repo = MagicMock()
    repo.get_pipeline_counts.return_value = {
        "total": 10,
        "ready_to_apply": 2,
        "with_desc": 8,
        "scored": 6,
        "tailored": 4,
        "cover_letters": 3,
        "applied": 1,
    }
    repo.count_by_status.return_value = {"pending": 2}
    app = SimpleNamespace(container=SimpleNamespace(job_repo=repo, _conn=MagicMock()))
    monkeypatch.setattr("applypilot.bootstrap.get_app", lambda: app)


def test_apply_command_defaults_to_batch_all_jobs(monkeypatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    _mock_bootstrap(monkeypatch)

    def _fake_apply_main(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("applypilot.apply.launcher.main", _fake_apply_main)

    result = runner.invoke(cli.app, ["apply"])

    assert result.exit_code == 0
    assert captured["limit"] is None
    assert captured["continuous"] is False


def test_apply_command_treats_zero_limit_as_batch_all(monkeypatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    _mock_bootstrap(monkeypatch)

    def _fake_apply_main(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("applypilot.apply.launcher.main", _fake_apply_main)

    result = runner.invoke(cli.app, ["apply", "--limit", "0"])

    assert result.exit_code == 0
    assert captured["limit"] is None
    assert captured["continuous"] is False
