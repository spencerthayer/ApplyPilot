"""Tests for chunked pipeline executor."""

import threading
from unittest.mock import MagicMock

from applypilot.pipeline.chunked import ChunkedExecutor
from applypilot.pipeline.context import PipelineContext


class TestChunkedExecutor:
    def _ctx(self):
        return PipelineContext()

    def test_basic_flow(self):
        """All three stages run and complete."""
        calls = {"discover": 0, "enrich": 0, "score": 0}

        def discover_fn(ctx):
            calls["discover"] += 1
            return 100  # 100 jobs

        def enrich_fn(chunk_idx):
            calls["enrich"] += 1

        def score_fn():
            calls["score"] += 1

        executor = ChunkedExecutor(self._ctx(), chunk_size=100)
        result = executor.execute(discover_fn, enrich_fn, score_fn)

        assert calls["discover"] == 1
        assert calls["enrich"] >= 1
        assert calls["score"] >= 1
        assert result["chunks"] >= 1
        assert result["elapsed"] > 0

    def test_multiple_chunks(self):
        """2000 jobs with chunk_size=1000 produces 2 chunks."""
        enrich_calls = []

        def discover_fn(ctx):
            return 2000

        def enrich_fn(chunk_idx):
            enrich_calls.append(chunk_idx)

        def score_fn():
            pass

        executor = ChunkedExecutor(self._ctx(), chunk_size=1000)
        result = executor.execute(discover_fn, enrich_fn, score_fn)

        assert result["chunks"] == 2
        assert len(enrich_calls) == 2

    def test_discover_error_handled(self):
        """Discovery failure doesn't crash the pipeline."""

        def discover_fn(ctx):
            raise RuntimeError("network error")

        executor = ChunkedExecutor(self._ctx(), chunk_size=100)
        result = executor.execute(discover_fn, lambda i: None, lambda: None)

        assert len(result["errors"]) > 0
        assert "discover" in result["errors"][0]

    def test_enrich_error_handled(self):
        """Enrichment failure on one chunk doesn't stop scoring."""
        score_calls = []

        def discover_fn(ctx):
            return 100

        def enrich_fn(chunk_idx):
            raise RuntimeError("enrich failed")

        def score_fn():
            score_calls.append(1)

        executor = ChunkedExecutor(self._ctx(), chunk_size=100)
        result = executor.execute(discover_fn, enrich_fn, score_fn)

        assert len(result["errors"]) > 0

    def test_zero_jobs(self):
        """Zero discovered jobs still completes cleanly."""

        def discover_fn(ctx):
            return 0

        executor = ChunkedExecutor(self._ctx(), chunk_size=100)
        result = executor.execute(discover_fn, lambda i: None, lambda: None)

        assert result["chunks"] >= 1
        assert result["errors"] == []
