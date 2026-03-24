"""Tests for pre-score title relevance filter."""

import pytest
from unittest.mock import patch

from applypilot.scoring.scorer import _load_target_title_keywords, evaluate_exclusion


MOCK_SEARCH_CONFIG = {
    "queries": [
        {"query": "Software Engineer"},
        {"query": "SDE"},
        {"query": "Android Engineer"},
        {"query": "Backend Engineer"},
        {"query": "Java Engineer"},
        {"query": "ML Engineer"},
        {"query": "DevOps Engineer"},
        {"query": "Cloud Engineer"},
    ],
    "exclude_titles": ["intern", "VP ", "vice president", "chief"],
}


@pytest.fixture(autouse=True)
def mock_config():
    with patch("applypilot.config.load_search_config", return_value=MOCK_SEARCH_CONFIG):
        yield


class TestLoadTargetKeywords:
    def test_returns_keywords_and_phrases(self):
        keywords, phrases = _load_target_title_keywords()
        assert isinstance(keywords, set)
        assert isinstance(phrases, list)
        assert len(phrases) > 0

    def test_abbreviation_expansion(self):
        _, phrases = _load_target_title_keywords()
        assert "machine learning engineer" in phrases  # ML → Machine Learning

    def test_generic_words_excluded(self):
        """Words appearing in >50% of queries are excluded from keywords."""
        keywords, _ = _load_target_title_keywords()
        # "engineer" appears in 6/8 queries (75%) — should be excluded
        assert "engineer" not in keywords

    def test_distinctive_words_included(self):
        keywords, _ = _load_target_title_keywords()
        assert "android" in keywords
        assert "backend" in keywords
        assert "java" in keywords


class TestEvaluateExclusion:
    def _job(self, title):
        return {"title": title, "site": "", "full_description": ""}

    def test_relevant_title_passes(self):
        assert evaluate_exclusion(self._job("Software Engineer III")) is None

    def test_android_passes(self):
        assert evaluate_exclusion(self._job("Android Engineer")) is None

    def test_backend_passes(self):
        assert evaluate_exclusion(self._job("Backend Engineer/API")) is None

    def test_ml_engineer_passes(self):
        """ML expands to Machine Learning — should match."""
        assert evaluate_exclusion(self._job("Machine Learning Engineer (L5)")) is None

    def test_devops_passes(self):
        assert evaluate_exclusion(self._job("DevOps Engineer")) is None

    def test_irrelevant_blocked(self):
        result = evaluate_exclusion(self._job("Account Manager"))
        assert result is not None
        assert result["exclusion_reason_code"] == "no_title_overlap"

    def test_senior_manager_blocked(self):
        result = evaluate_exclusion(self._job("Senior Manager, Technical Support"))
        assert result is not None

    def test_saas_operator_blocked(self):
        result = evaluate_exclusion(self._job("SaaS Operator / Builder"))
        assert result is not None

    def test_intern_blocked_by_exclude_titles(self):
        result = evaluate_exclusion(self._job("Software Engineer Intern"))
        assert result is not None
        assert "excluded" in result["exclusion_reason_code"]

    def test_vp_blocked(self):
        result = evaluate_exclusion(self._job("VP of Engineering"))
        assert result is not None

    def test_unknown_role_passes(self):
        """Unknown/missing titles should pass — let LLM decide."""
        assert evaluate_exclusion(self._job("Unknown Role")) is None

    def test_empty_title_passes(self):
        assert evaluate_exclusion(self._job("")) is None
