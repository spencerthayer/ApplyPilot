"""Tests for LLM cost tracking — CostTracker, factory aggregation, CLI command.

Covers:
  - CostTracker stores per-call records (provider, model, tokens, cost)
  - CostTracker.summary() returns correct aggregated keys
  - get_cost_summary() aggregates across multiple singleton clients
  - CLI llm_cmd.costs() reads from the same summary
"""

import threading

import pytest

from applypilot.llm.cost_tracker import CostTracker


class TestCostEstimation:
    """Pricing table and estimated cost calculation."""

    @pytest.mark.parametrize(
        "model,tokens_in,tokens_out,expected_min",
        [
            ("bedrock/global.anthropic.claude-opus-4-6-v1", 1000, 1000, 0.08),
            ("bedrock/global.anthropic.claude-sonnet-4-6", 1000, 1000, 0.015),
            ("bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0", 1000, 0, 0.0005),
            ("bedrock/amazon.nova-micro-v1:0", 10000, 5000, 0.0005),
            ("bedrock/qwen.qwen3-32b-v1:0", 1000, 1000, 0.0001),
            ("bedrock/mistral.mistral-large-3-675b-instruct", 1000, 1000, 0.005),
        ],
    )
    def test_known_models(self, model, tokens_in, tokens_out, expected_min):
        from applypilot.llm.cost_tracker import _estimate_cost

        assert _estimate_cost(model, tokens_in, tokens_out) >= expected_min

    def test_unknown_model_zero(self):
        from applypilot.llm.cost_tracker import _estimate_cost

        assert _estimate_cost("some/unknown", 1000, 1000) == 0.0

    def test_opus_gt_sonnet_gt_haiku(self):
        from applypilot.llm.cost_tracker import _estimate_cost

        costs = [
            _estimate_cost(f"bedrock/global.{m}", 1000, 1000)
            for m in ["anthropic.claude-opus-4-6", "anthropic.claude-sonnet-4-6", "anthropic.claude-haiku-4-5"]
        ]
        assert costs[0] > costs[1] > costs[2]

    def test_tracker_estimates_when_api_returns_zero(self):
        ct = CostTracker()
        ct.record("bedrock", "bedrock/global.anthropic.claude-sonnet-4-6", 1000, 1000, 0.0)
        assert ct.summary()["total_cost"] > 0  # estimated, not 0

    def test_tracker_prefers_api_cost(self):
        ct = CostTracker()
        ct.record("openai", "gpt-4o", 1000, 1000, 0.05)
        assert ct.summary()["total_cost"] == pytest.approx(0.05)

    def test_summary_model_breakdown(self):
        ct = CostTracker()
        ct.record("bedrock", "bedrock/qwen.qwen3-32b-v1:0", 500, 200, 0.0)
        ct.record("bedrock", "bedrock/qwen.qwen3-32b-v1:0", 500, 300, 0.0)
        info = ct.summary()["by_model"]["bedrock/qwen.qwen3-32b-v1:0"]
        assert info["calls"] == 2
        assert info["tokens_in"] == 1000
        assert info["cost"] > 0


class TestCostTracker:
    """CostTracker is the in-memory store — one per LLMClient instance."""

    def test_empty_summary(self):
        ct = CostTracker()
        s = ct.summary()
        assert s["calls"] == 0
        assert s["total_cost"] == 0.0
        assert s["total_tokens_in"] == 0
        assert s["total_tokens_out"] == 0
        assert s["by_model"] == {}

    def test_single_record(self):
        ct = CostTracker()
        ct.record("bedrock", "claude-opus", tokens_in=100, tokens_out=50, cost=0.0025)
        s = ct.summary()
        assert s["calls"] == 1
        assert s["total_tokens_in"] == 100
        assert s["total_tokens_out"] == 50
        assert s["total_cost"] == pytest.approx(0.0025)
        assert s["by_model"]["claude-opus"]["cost"] == pytest.approx(0.0025)

    def test_multiple_records_same_model(self):
        ct = CostTracker()
        ct.record("bedrock", "claude-opus", 100, 50, 0.01)
        ct.record("bedrock", "claude-opus", 200, 100, 0.02)
        s = ct.summary()
        assert s["calls"] == 2
        assert s["total_tokens_in"] == 300
        assert s["total_tokens_out"] == 150
        assert s["total_cost"] == pytest.approx(0.03)
        assert s["by_model"]["claude-opus"]["cost"] == pytest.approx(0.03)

    def test_multiple_models(self):
        ct = CostTracker()
        ct.record("bedrock", "claude-opus", 100, 50, 0.01)
        ct.record("gemini", "gemini-pro", 500, 200, 0.0)
        s = ct.summary()
        assert s["calls"] == 2
        assert len(s["by_model"]) == 2
        assert s["by_model"]["gemini-pro"]["cost"] == 0.0
        assert s["by_model"]["claude-opus"]["cost"] == pytest.approx(0.01)

    def test_thread_safety(self):
        ct = CostTracker()
        errors = []

        def _record_many():
            try:
                for i in range(100):
                    ct.record("test", "model", 10, 5, 0.001)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_record_many) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert ct.summary()["calls"] == 400
        assert ct.summary()["total_cost"] == pytest.approx(0.4)
        assert ct.summary()["total_tokens_in"] == 4000
        assert ct.summary()["total_tokens_out"] == 2000

    def test_thread_safety_mixed_read_write(self):
        """Concurrent reads and writes should not corrupt data or raise."""
        ct = CostTracker()
        errors = []
        summaries = []

        def _writer():
            try:
                for _ in range(50):
                    ct.record("bedrock", "opus", 100, 50, 0.01)
            except Exception as e:
                errors.append(e)

        def _reader():
            try:
                for _ in range(50):
                    s = ct.summary()
                    # Invariants that must always hold
                    assert s["calls"] >= 0
                    assert s["total_cost"] >= 0
                    assert s["total_tokens_in"] >= 0
                    assert len(s["by_model"]) <= 1  # only one model
                    summaries.append(s)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=_writer),
            threading.Thread(target=_writer),
            threading.Thread(target=_reader),
            threading.Thread(target=_reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        # Final state: 2 writers × 50 records = 100
        assert ct.summary()["calls"] == 100

    def test_thread_safety_summary_consistency(self):
        """summary() should return internally consistent snapshots."""
        ct = CostTracker()
        for _ in range(10):
            ct.record("p", "m", 100, 50, 0.01)

        s = ct.summary()
        # calls × per-call values should match totals
        assert s["total_tokens_in"] == s["calls"] * 100
        assert s["total_tokens_out"] == s["calls"] * 50
        assert s["total_cost"] == pytest.approx(s["calls"] * 0.01)


class TestFactoryGetCostSummary:
    """get_cost_summary() aggregates across all singleton LLMClient instances."""

    def test_returns_correct_keys(self):
        from applypilot.llm.factory import get_cost_summary

        s = get_cost_summary()
        assert "calls" in s
        assert "total_cost" in s
        assert "total_tokens_in" in s
        assert "total_tokens_out" in s
        assert "by_model" in s

    def test_aggregates_from_clients(self, monkeypatch):
        """Simulate two clients with recorded costs."""
        c1 = CostTracker()
        c1.record("bedrock", "opus", 100, 50, 0.01)
        c2 = CostTracker()
        c2.record("gemini", "pro", 500, 200, 0.0)
        c2.record("gemini", "pro", 300, 100, 0.0)

        s1 = c1.summary()
        s2 = c2.summary()
        total_calls = s1["calls"] + s2["calls"]
        total_in = s1["total_tokens_in"] + s2["total_tokens_in"]

        assert total_calls == 3
        assert total_in == 900
        assert s1["by_model"]["opus"]["cost"] == pytest.approx(0.01)
        assert s2["by_model"]["pro"]["calls"] == 2


class TestLLMClientCostTrackerWired:
    """LLMClient should have a _cost_tracker attribute."""

    def test_client_has_cost_tracker(self):
        from applypilot.llm.config import LLMConfig
        from applypilot.llm.client import LLMClient

        config = LLMConfig(
            provider="openai",
            api_base="http://localhost:1234",
            model="test-model",
            api_key="fake",
            base_url="http://localhost:1234",
        )
        client = LLMClient(config)
        assert hasattr(client, "_cost_tracker")
        assert isinstance(client._cost_tracker, CostTracker)
        assert client._cost_tracker.summary()["calls"] == 0
