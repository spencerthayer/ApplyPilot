"""Tests for apply/code_filler.py — HTTP prefetch and field matching."""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock
from applypilot.apply.code_filler import (
    prefetch_page,
    _match_field_to_profile,
    build_profile_data,
)


class TestPrefetchPage:
    """Phase 1 HTTP pre-fetch."""

    def test_live_greenhouse_with_fields(self):
        html = """<html><title>Job at Acme</title><body>
        <form id="application-form">
        <input id="first_name" type="text">
        <input id="last_name" type="text">
        <input id="email" type="text">
        <input id="phone" type="tel">
        <input id="resume" type="file">
        </form></body></html>"""
        with patch("applypilot.apply.code_filler.httpx.get") as mock:
            mock.return_value = MagicMock(status_code=200, text=html)
            r = prefetch_page("https://example.com/jobs/123")
        assert r["status"] == "live"
        assert len(r["fields"]) == 5
        assert r["fields"][0]["id"] == "first_name"

    def test_expired_404(self):
        with patch("applypilot.apply.code_filler.httpx.get") as mock:
            mock.return_value = MagicMock(status_code=404, text="<html>Not Found</html>")
            r = prefetch_page("https://example.com/jobs/old")
        assert r["status"] == "expired"

    def test_expired_text_pattern(self):
        html = "<html><body>This job is no longer accepting applications.</body></html>"
        with patch("applypilot.apply.code_filler.httpx.get") as mock:
            mock.return_value = MagicMock(status_code=200, text=html)
            r = prefetch_page("https://example.com/jobs/closed")
        assert r["status"] == "expired"

    def test_login_required(self):
        html = '<html><body>Sign in <input type="password"> forgot password</body></html>'
        with patch("applypilot.apply.code_filler.httpx.get") as mock:
            mock.return_value = MagicMock(status_code=200, text=html)
            r = prefetch_page("https://example.com/jobs/auth")
        assert r["status"] == "login_required"

    def test_http_error(self):
        with patch("applypilot.apply.code_filler.httpx.get") as mock:
            mock.side_effect = Exception("timeout")
            r = prefetch_page("https://example.com/jobs/down")
        assert r["status"] == "error"
        assert "timeout" in r["error"]

    def test_skips_hidden_and_submit(self):
        html = """<html><body>
        <input id="token" type="hidden" value="abc">
        <input id="name" type="text">
        <input type="submit" value="Go">
        </body></html>"""
        with patch("applypilot.apply.code_filler.httpx.get") as mock:
            mock.return_value = MagicMock(status_code=200, text=html)
            r = prefetch_page("https://example.com/jobs/1")
        assert len(r["fields"]) == 1
        assert r["fields"][0]["id"] == "name"

    def test_label_for_extraction(self):
        html = """<html><body>
        <label for="q1">What is your name?</label>
        <input id="q1" type="text">
        </body></html>"""
        with patch("applypilot.apply.code_filler.httpx.get") as mock:
            mock.return_value = MagicMock(status_code=200, text=html)
            r = prefetch_page("https://example.com/jobs/1")
        assert r["fields"][0]["label"] == "What is your name?"


class TestFieldMatching:
    """Profile field → form field matching."""

    PROFILE = {
        "first_name": "John",
        "last_name": "Doe",
        "email": "john@example.com",
        "phone": "+1234567890",
        "city": "London",
        "linkedin": "https://linkedin.com/in/johndoe",
        "github": "https://github.com/johndoe",
        "salary": "100000 USD",
        "available_start": "Immediately",
        "how_heard": "Online Job Board",
        "work_authorized": "Yes",
        "sponsorship_needed": "No",
    }

    def test_id_match_greenhouse(self):
        assert _match_field_to_profile({"id": "first_name"}, self.PROFILE) == "John"

    def test_id_match_lever(self):
        assert _match_field_to_profile({"id": "resumator-email-value"}, self.PROFILE) == "john@example.com"

    def test_label_match(self):
        assert _match_field_to_profile({"id": "q1", "label": "LinkedIn Profile"}, self.PROFILE) == "https://linkedin.com/in/johndoe"

    def test_label_match_github(self):
        assert _match_field_to_profile({"id": "q2", "label": "GitHub Link"}, self.PROFILE) == "https://github.com/johndoe"

    def test_screening_how_heard(self):
        assert _match_field_to_profile({"id": "q3", "label": "How did you hear about this job?"}, self.PROFILE) == "Online Job Board"

    def test_screening_age(self):
        assert _match_field_to_profile({"id": "q4", "label": "Are you 18 years of age or older?"}, self.PROFILE) == "Yes"

    def test_screening_work_auth(self):
        result = _match_field_to_profile({"id": "q5", "label": "Are you legally authorized to work?"}, self.PROFILE)
        assert result == "Yes"

    def test_screening_sponsorship(self):
        result = _match_field_to_profile({"id": "q6", "label": "Will you require sponsorship?"}, self.PROFILE)
        assert result == "No"

    def test_screening_gender(self):
        result = _match_field_to_profile({"id": "q7", "label": "Gender"}, self.PROFILE)
        assert result == "Decline to self-identify"

    def test_no_match(self):
        assert _match_field_to_profile({"id": "q99", "label": "Favorite color?"}, self.PROFILE) is None

    def test_salary_match(self):
        assert _match_field_to_profile({"id": "q10", "label": "Desired salary"}, self.PROFILE) == "100000 USD"


class TestBuildProfileData:
    """Profile data builder."""

    def test_builds_from_profile(self):
        mock_profile = {
            "personal": {"full_name": "Jane Smith", "email": "jane@test.com", "phone": "+44123"},
            "work_authorization": {"legally_authorized_to_work": True, "require_sponsorship": False},
            "compensation": {"salary_expectation": "80000", "salary_currency": "GBP"},
            "experience": {"current_company": "Acme"},
            "availability": {"earliest_start_date": "2026-05-01"},
        }
        with patch("applypilot.config.load_profile", return_value=mock_profile):
            data = build_profile_data({})
        assert data["first_name"] == "Jane"
        assert data["last_name"] == "Smith"
        assert data["email"] == "jane@test.com"
        assert data["salary"] == "80000 GBP"
        assert data["available_start"] == "2026-05-01"
