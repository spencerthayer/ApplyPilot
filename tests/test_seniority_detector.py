"""Tests for scoring/deterministic/seniority_detector.py."""


class TestSeniorityDetector:
    def test_seniority_from_text(self):
        from applypilot.scoring.deterministic.seniority_detector import seniority_from_text

        assert seniority_from_text("Senior Software Engineer") >= 3
        assert seniority_from_text("Junior Developer") <= 2
        assert seniority_from_text("Staff Engineer") >= 4

    def test_patterns_exist(self):
        from applypilot.scoring.deterministic.seniority_detector import SENIORITY_PATTERNS

        assert len(SENIORITY_PATTERNS) > 0
