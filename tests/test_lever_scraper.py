"""Tests for the Lever ATS direct-API scraper."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ── Location composition ────────────────────────────────────────────────────

def test_location_string_includes_workplace_type():
    from applypilot.discovery.lever import _location_string
    posting = {
        "categories": {"location": "Seattle, WA"},
        "workplaceType": "hybrid",
    }
    assert _location_string(posting) == "hybrid, Seattle, WA"


def test_location_string_remote_workplace_type():
    """Remote workplaceType should land in the location string so the
    location filter's remote-allowlist fires (location=remote → kept)."""
    from applypilot.discovery.lever import _location_string
    posting = {
        "categories": {"location": "United States"},
        "workplaceType": "remote",
    }
    composed = _location_string(posting)
    assert "remote" in composed.lower()
    assert "United States" in composed


def test_location_string_skips_onsite_label():
    """workplaceType="on-site" doesn't get prepended (it's the default)."""
    from applypilot.discovery.lever import _location_string
    posting = {
        "categories": {"location": "Seattle, WA"},
        "workplaceType": "on-site",
    }
    assert _location_string(posting) == "Seattle, WA"


def test_location_string_dedupes_alllocations():
    from applypilot.discovery.lever import _location_string
    posting = {
        "categories": {
            "location": "Seattle, WA",
            "allLocations": ["Seattle, WA", "Bellevue, WA"],
        },
    }
    out = _location_string(posting)
    # Seattle should appear once, Bellevue once
    assert out.count("Seattle") == 1
    assert "Bellevue" in out


def test_location_string_blank_inputs():
    from applypilot.discovery.lever import _location_string
    assert _location_string({}) == ""
    assert _location_string({"categories": {}}) == ""


# ── Description composition ────────────────────────────────────────────────

def test_description_text_combines_html_and_lists():
    from applypilot.discovery.lever import _description_text
    posting = {
        "description": "<p>About the role.</p>",
        "lists": [
            {"text": "What you'll do",
             "content": "<ul><li>Ship code</li><li>Mentor</li></ul>"},
            {"text": "Requirements",
             "content": "<ul><li>5y experience</li></ul>"},
        ],
        "additional": "<p>Equal opportunity employer.</p>",
    }
    out = _description_text(posting)
    assert "About the role" in out
    assert "What you'll do" in out
    assert "Ship code" in out
    assert "Requirements" in out
    assert "5y experience" in out
    assert "Equal opportunity" in out


def test_description_text_handles_empty_posting():
    from applypilot.discovery.lever import _description_text
    assert _description_text({}) == ""


# ── End-to-end with mocked HTTP fetch ──────────────────────────────────────

@pytest.fixture
def mock_lever_api(monkeypatch):
    """Patch the shared fetch helper to return canned data."""
    from applypilot.discovery import ats_common
    captured = {"url": None}

    def fake_fetch(url, timeout=20.0):
        captured["url"] = url
        return [
            {
                "id": "abc-123",
                "text": "Senior Software Engineer",
                "categories": {
                    "location": "Seattle, WA",
                    "team": "Engineering",
                    "commitment": "Full-time",
                },
                "workplaceType": "hybrid",
                "description": "<p>Join us building stuff.</p>",
                "lists": [
                    {"text": "What you'll do",
                     "content": "<ul><li>Ship code</li></ul>"}
                ],
                "additional": "",
                "applyUrl": "https://jobs.lever.co/acme/abc-123/apply",
                "hostedUrl": "https://jobs.lever.co/acme/abc-123",
                "createdAt": 1745000000000,
            },
            {
                "id": "def-456",
                "text": "Sales Director",
                "categories": {"location": "Tokyo, Japan"},  # outside accept
                "description": "Sales role",
                "applyUrl": "https://jobs.lever.co/acme/def-456/apply",
                "hostedUrl": "https://jobs.lever.co/acme/def-456",
            },
        ]

    monkeypatch.setattr(ats_common, "_fetch_json", fake_fetch)
    return captured


def test_scrape_one_employer_filters_by_location(mock_lever_api):
    from applypilot.discovery.lever import scrape_one_employer
    jobs, err = scrape_one_employer(
        "acme", {"name": "Acme"},
        accept_locs=["seattle", "remote", "wa"],
    )
    assert err is None
    # Tokyo posting filtered out, Seattle posting kept
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Senior Software Engineer"
    assert j["url"] == "https://jobs.lever.co/acme/abc-123"
    assert j["application_url"] == "https://jobs.lever.co/acme/abc-123/apply"
    assert "Seattle" in j["location"]
    # createdAt 1745000000000 ms → 2025-04-18T13:13:20+00:00
    assert j["posted_at"].startswith("2025-04-18")


def test_scrape_one_employer_hits_correct_endpoint(mock_lever_api):
    from applypilot.discovery.lever import scrape_one_employer
    scrape_one_employer("highspot", {"name": "Highspot"}, accept_locs=[])
    assert mock_lever_api["url"] == "https://api.lever.co/v0/postings/highspot?mode=json"


def test_load_employers_reads_yaml():
    """Sanity-check the registry file is shipped and parsable."""
    from applypilot.discovery.lever import load_employers
    employers = load_employers()
    # The bootstrap registry has at least the 4 verified slugs.
    assert "highspot" in employers
    assert "outreach" in employers
    assert "rover" in employers
    assert "plaid" in employers
    assert employers["highspot"]["name"] == "Highspot"
