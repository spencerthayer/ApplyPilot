"""Tests for pipeline limit handling.

Verifies that run_pipeline passes limit=0 by default (unbounded)
and respects explicit limit overrides.
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import applypilot.pipeline as pipeline


def test_run_pipeline_defaults_to_unbounded_tailor_cover_limit(monkeypatch) -> None:
    captured: dict = {}

    def _fake_execute(self):
        captured["limit"] = self._ctx.limit
        return {"stages": [], "elapsed": 0.0, "errors": []}

    with patch.object(pipeline.Pipeline, "execute", _fake_execute):
        pipeline.run_pipeline(stages=["tailor"], stream=False)

    assert captured["limit"] == 0


def test_run_pipeline_respects_explicit_limit_override(monkeypatch) -> None:
    captured: dict = {}

    def _fake_execute(self):
        captured["limit"] = self._ctx.limit
        return {"stages": [], "elapsed": 0.0, "errors": []}

    with patch.object(pipeline.Pipeline, "execute", _fake_execute):
        pipeline.run_pipeline(stages=["tailor"], limit=5, stream=False)

    assert captured["limit"] == 5
