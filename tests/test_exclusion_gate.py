"""Tests for scoring/deterministic/exclusion_gate.py."""


class TestExclusionGate:
    def test_exclude_intern(self):
        from applypilot.scoring.deterministic.exclusion_gate import evaluate_exclusion

        result = evaluate_exclusion({"title": "Software Engineering Intern"})
        assert result is not None
        assert result["score"] == 0
        assert "intern" in result["reasoning"].lower()

    def test_exclude_clearance(self):
        from applypilot.scoring.deterministic.exclusion_gate import evaluate_exclusion

        result = evaluate_exclusion({"title": "SDE", "full_description": "Must have active clearance"})
        assert result is not None
        assert result["score"] == 0

    def test_pass_normal_job(self):
        from applypilot.scoring.deterministic.exclusion_gate import evaluate_exclusion

        result = evaluate_exclusion({"title": "Software Engineer"})
        if result is not None:
            assert result.get("exclusion_reason_code") != "excluded_keyword"

    def test_exclusion_result_format(self):
        from applypilot.scoring.deterministic.exclusion_gate import exclusion_result

        rule = {"id": "r-test", "reason_code": "test_reason", "description": "Test"}
        r = exclusion_result(rule, "matched_val")
        assert r["score"] == 0
        assert "test_reason" in r["reasoning"]
        assert r["exclusion_rule_id"] == "r-test"
