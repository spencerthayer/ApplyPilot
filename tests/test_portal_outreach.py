"""Tests for Ashby, Lever scrapers and LinkedIn outreach."""

from applypilot.discovery.ashby import parse_jobs as ashby_parse
from applypilot.discovery.lever import parse_jobs as lever_parse
from applypilot.outreach.linkedin import generate_outreach


class TestAshbyParser:
    def test_parse_valid_jobs(self):
        raw = [
            {"title": "Backend Engineer", "jobUrl": "https://jobs.ashbyhq.com/co/123", "location": "Remote"},
            {"title": "", "jobUrl": ""},  # should be skipped
        ]
        results = ashby_parse(raw, "TestCo")
        assert len(results) == 1
        assert results[0]["title"] == "Backend Engineer"
        assert results[0]["site"] == "ashby"

    def test_parse_empty(self):
        assert ashby_parse([], "Co") == []


class TestLeverParser:
    def test_parse_valid_jobs(self):
        raw = [
            {"text": "SDE", "hostedUrl": "https://jobs.lever.co/co/123", "categories": {"location": "NYC"}},
        ]
        results = lever_parse(raw, "TestCo")
        assert len(results) == 1
        assert results[0]["site"] == "lever"

    def test_parse_empty(self):
        assert lever_parse([], "Co") == []


class TestLinkedInOutreach:
    def test_generates_for_linkedin(self):
        job = {
            "url": "https://linkedin.com/jobs/view/123",
            "title": "SDE",
            "company": "Google",
            "matched_skills": ["Python"],
        }
        draft = generate_outreach(job, "Built DDD system reducing costs by 75%")
        assert draft is not None
        assert draft["char_count"] <= 300
        assert "Google" in draft["message"]

    def test_skips_non_linkedin(self):
        job = {"url": "https://careers.dexcom.com/job/123", "title": "SDE"}
        assert generate_outreach(job, "bullet") is None
