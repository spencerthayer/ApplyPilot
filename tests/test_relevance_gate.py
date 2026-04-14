"""Tests for discovery/relevance_gate.py"""

from __future__ import annotations

import pytest
from unittest.mock import patch


class TestIsRelevant:
    """Profile-driven relevance gate."""

    @pytest.fixture(autouse=True)
    def _mock_filters(self):
        """Mock profile filters so tests don't need real resume.json."""
        filters = {
            "role_keywords": {"engineer", "developer", "sde", "android", "software", "devops", "architect", "backend"},
            "anti_keywords": {"sales", "recruiter", "nurse", "marketing", "hr", "customer support"},
            "exclude_countries": ["us"],
            "yoe": 4,
            "has_llm_filter": True,
        }
        with patch("applypilot.discovery.relevance_gate._load_profile_filters", return_value=filters):
            from applypilot.discovery.relevance_gate import clear_cache
            clear_cache()
            yield

    # ── Anti-keywords ─────────────────────────────────────────────────

    def test_anti_keyword_sales(self):
        from applypilot.discovery.relevance_gate import is_relevant
        assert is_relevant("Sales Development Representative", "Singapore") is False

    def test_anti_keyword_recruiter(self):
        from applypilot.discovery.relevance_gate import is_relevant
        assert is_relevant("Technical Recruiter", "London, UK") is False

    def test_anti_keyword_nurse(self):
        from applypilot.discovery.relevance_gate import is_relevant
        assert is_relevant("Registered Nurse", "Toronto, Canada") is False

    def test_no_anti_match_engineer(self):
        from applypilot.discovery.relevance_gate import is_relevant
        assert is_relevant("Senior Software Engineer", "Barcelona, Spain") is True

    # ── Role keywords ─────────────────────────────────────────────────

    def test_role_match_developer(self):
        from applypilot.discovery.relevance_gate import is_relevant
        assert is_relevant("Full Stack Developer", "Berlin, Germany") is True

    def test_role_match_android(self):
        from applypilot.discovery.relevance_gate import is_relevant
        assert is_relevant("Android Engineer", "Bangalore, India") is True

    def test_role_no_match(self):
        from applypilot.discovery.relevance_gate import is_relevant
        assert is_relevant("Events Coordinator", "Tokyo, Japan") is False

    def test_role_no_match_product_manager(self):
        from applypilot.discovery.relevance_gate import is_relevant
        assert is_relevant("Product Manager", "Singapore") is False

    # ── Location exclusion ────────────────────────────────────────────

    def test_exclude_us_california(self):
        from applypilot.discovery.relevance_gate import is_relevant
        with patch("applypilot.discovery.location_resolver.resolve_country", return_value="US"):
            assert is_relevant("Software Engineer", "Palo Alto, California") is False

    def test_exclude_us_remote_nc(self):
        from applypilot.discovery.relevance_gate import is_relevant
        with patch("applypilot.discovery.location_resolver.resolve_country", return_value="US"):
            assert is_relevant("Software Engineer", "Remote - North Carolina") is False

    def test_allow_non_excluded(self):
        from applypilot.discovery.relevance_gate import is_relevant
        with patch("applypilot.discovery.location_resolver.resolve_country", return_value="IN"):
            assert is_relevant("Software Engineer", "Bangalore, India") is True

    def test_allow_no_location(self):
        from applypilot.discovery.relevance_gate import is_relevant
        assert is_relevant("Software Engineer", "") is True

    # ── YOE gate ──────────────────────────────────────────────────────

    def test_yoe_within_range(self):
        from applypilot.discovery.relevance_gate import is_relevant
        assert is_relevant("Software Engineer", "London, UK", "3+ years of experience required") is True

    def test_yoe_at_boundary(self):
        """profile=4, required=6, gap=2 → allowed (threshold is yoe+2)."""
        from applypilot.discovery.relevance_gate import is_relevant
        assert is_relevant("Software Engineer", "London, UK", "6+ years of experience") is True

    def test_yoe_exceeds(self):
        """profile=4, required=7, gap=3 → rejected."""
        from applypilot.discovery.relevance_gate import is_relevant
        assert is_relevant("Software Engineer", "London, UK", "7+ years of experience in backend") is False

    def test_yoe_minimum_format(self):
        from applypilot.discovery.relevance_gate import is_relevant
        assert is_relevant("Software Engineer", "London, UK", "minimum 8 years experience") is False

    def test_yoe_no_description(self):
        from applypilot.discovery.relevance_gate import is_relevant
        assert is_relevant("Software Engineer", "London, UK", "") is True

    # ── No profile loaded ─────────────────────────────────────────────

    def test_no_profile_accepts_all(self):
        from applypilot.discovery.relevance_gate import is_relevant
        with patch("applypilot.discovery.relevance_gate._load_profile_filters", return_value={}):
            assert is_relevant("Anything", "Anywhere", "whatever") is True


class TestGenerateRelevanceFilter:
    """LLM-generated relevance filter."""

    def test_generate_returns_keywords(self):
        """Mock LLM to verify generate_relevance_filter structure."""
        from applypilot.discovery.relevance_gate import generate_relevance_filter

        mock_response = '{"role_keywords": ["engineer", "developer"], "anti_keywords": ["sales"]}'
        with patch("applypilot.llm.get_client") as mock_client:
            mock_client.return_value.chat.return_value = mock_response
            result = generate_relevance_filter({
                "meta": {"applypilot": {"target_role": "Software Engineer"}},
                "skills": [{"keywords": ["Python", "Java"]}],
                "work": [{"position": "SDE"}],
            })
            assert "engineer" in result["role_keywords"]
            assert "sales" in result["anti_keywords"]

    def test_generate_handles_llm_failure(self):
        from applypilot.discovery.relevance_gate import generate_relevance_filter

        with patch("applypilot.llm.get_client") as mock_client:
            mock_client.return_value.chat.side_effect = RuntimeError("no LLM")
            result = generate_relevance_filter({"meta": {"applypilot": {}}})
            assert result == {}
