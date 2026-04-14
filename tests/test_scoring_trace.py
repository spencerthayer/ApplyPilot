"""Tests for scoring/trace.py."""


class TestScoringTrace:
    def test_coerce_text(self):
        from applypilot.scoring.trace import coerce_text

        assert coerce_text(None) == ""
        assert coerce_text("  hello  ") == "hello"
        assert coerce_text(42) == "42"

    def test_coerce_list(self):
        from applypilot.scoring.trace import coerce_list

        assert coerce_list(None) == []
        assert coerce_list("") == []
        assert coerce_list(["a", "b"]) == ["a", "b"]
        assert coerce_list("single") == ["single"]

    def test_to_float(self):
        from applypilot.scoring.trace import to_float

        assert to_float(None) is None
        assert to_float(3.5) == 3.5
        assert to_float("0.85") == 0.85
        assert to_float("score: 7") == 7.0

    def test_safe_response_snippet(self):
        from applypilot.scoring.trace import safe_response_snippet

        assert safe_response_snippet("short") == "short"
        long = "x" * 500
        snip = safe_response_snippet(long, limit=50)
        assert len(snip) == 50
        assert snip.endswith("...")

    def test_is_trace_enabled(self):
        from applypilot.scoring.trace import is_trace_enabled

        assert isinstance(is_trace_enabled(), bool)
