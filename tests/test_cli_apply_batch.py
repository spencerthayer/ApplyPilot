from __future__ import annotations

from typer.testing import CliRunner

import applypilot.cli as cli
from applypilot.apply import launcher


class _FakeCursor:
    def __init__(self, row: tuple[int]) -> None:
        self._row = row

    def fetchone(self) -> tuple[int]:
        return self._row


class _FakeConn:
    def execute(self, _query: str) -> _FakeCursor:
        return _FakeCursor((2,))


def test_apply_command_defaults_to_batch_all_jobs(monkeypatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "_bootstrap", lambda: None)
    monkeypatch.setattr(cli, "_resolve_backend_option", lambda *_args: ("codex", None))
    monkeypatch.setattr("applypilot.config.check_tier", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("applypilot.config.load_profile", lambda: {"ok": True})
    monkeypatch.setattr("applypilot.database.get_connection", lambda: _FakeConn())

    def _fake_apply_main(**kwargs):  # noqa: ANN003
        captured.update(kwargs)

    monkeypatch.setattr("applypilot.apply.launcher.main", _fake_apply_main)

    result = runner.invoke(cli.app, ["apply"])

    assert result.exit_code == 0
    assert captured["limit"] is None
    assert captured["continuous"] is False


def test_apply_command_treats_zero_limit_as_batch_all(monkeypatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "_bootstrap", lambda: None)
    monkeypatch.setattr(cli, "_resolve_backend_option", lambda *_args: ("codex", None))
    monkeypatch.setattr("applypilot.config.check_tier", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("applypilot.config.load_profile", lambda: {"ok": True})
    monkeypatch.setattr("applypilot.database.get_connection", lambda: _FakeConn())

    def _fake_apply_main(**kwargs):  # noqa: ANN003
        captured.update(kwargs)

    monkeypatch.setattr("applypilot.apply.launcher.main", _fake_apply_main)

    result = runner.invoke(cli.app, ["apply", "--limit", "0"])

    assert result.exit_code == 0
    assert captured["limit"] is None
    assert captured["continuous"] is False


def test_worker_loop_without_limit_drains_current_queue(monkeypatch) -> None:
    jobs = [
        {"url": "https://example.com/jobs/1", "title": "Job One", "site": "Example"},
        {"url": "https://example.com/jobs/2", "title": "Job Two", "site": "Example"},
        None,
    ]
    marked: list[tuple[str, str]] = []

    monkeypatch.setattr(
        launcher,
        "acquire_job",
        lambda **_kwargs: jobs.pop(0),
    )
    monkeypatch.setattr(launcher, "launch_chrome", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(launcher, "cleanup_worker", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        launcher,
        "run_job",
        lambda job, **_kwargs: ("applied", 1000),
    )
    monkeypatch.setattr(
        launcher,
        "mark_result",
        lambda url, status, **_kwargs: marked.append((url, status)),
    )
    monkeypatch.setattr(launcher, "update_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "add_event", lambda *_args, **_kwargs: None)

    launcher._stop_event.clear()
    applied, failed = launcher.worker_loop(limit=None, continuous=False)

    assert (applied, failed) == (2, 0)
    assert marked == [
        ("https://example.com/jobs/1", "applied"),
        ("https://example.com/jobs/2", "applied"),
    ]


def test_worker_loop_zero_limit_is_noop(monkeypatch) -> None:
    monkeypatch.setattr(
        launcher,
        "acquire_job",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("acquire_job should not be called")),
    )
    monkeypatch.setattr(launcher, "update_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "add_event", lambda *_args, **_kwargs: None)

    launcher._stop_event.clear()
    applied, failed = launcher.worker_loop(limit=0, continuous=False)

    assert (applied, failed) == (0, 0)
