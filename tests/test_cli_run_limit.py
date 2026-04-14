from __future__ import annotations

from typer.testing import CliRunner

import applypilot.cli as cli
import applypilot.pipeline as pipeline_mod


def test_run_command_forwards_default_limit(monkeypatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "_bootstrap", lambda: None)
    monkeypatch.setattr("applypilot.config.check_tier", lambda *_args, **_kwargs: None)

    _orig_batch = pipeline_mod.Pipeline.batch

    @classmethod
    def _fake_batch(cls, **kwargs):
        captured.update(kwargs)

        class _Fake:
            def execute(self):
                return {"stages": [], "errors": {}, "elapsed": 0.0}

        return _Fake()

    monkeypatch.setattr(pipeline_mod.Pipeline, "batch", _fake_batch)

    result = runner.invoke(cli.app, ["run", "tailor"])
    assert result.exit_code == 0
    assert captured["limit"] == 0


def test_run_command_forwards_explicit_limit(monkeypatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "_bootstrap", lambda: None)
    monkeypatch.setattr("applypilot.config.check_tier", lambda *_args, **_kwargs: None)

    @classmethod
    def _fake_batch(cls, **kwargs):
        captured.update(kwargs)

        class _Fake:
            def execute(self):
                return {"stages": [], "errors": {}, "elapsed": 0.0}

        return _Fake()

    monkeypatch.setattr(pipeline_mod.Pipeline, "batch", _fake_batch)

    result = runner.invoke(cli.app, ["run", "tailor", "--limit", "15"])
    assert result.exit_code == 0
    assert captured["limit"] == 15
