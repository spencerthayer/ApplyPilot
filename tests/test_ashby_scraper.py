"""Tests for the Ashby ATS direct-API scraper."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ── Location composition ────────────────────────────────────────────────────

def test_location_string_remote_workplace_type():
    """workplaceType=remote should land "remote" first so the location filter's
    remote-allowlist fires."""
    from applypilot.discovery.ashby import _location_string
    posting = {
        "location": "United States",
        "workplaceType": "Remote",
        "isRemote": True,
        "secondaryLocations": [],
    }
    assert _location_string(posting).lower().startswith("remote")


def test_location_string_isremote_alone():
    """isRemote=True should also surface 'remote' even if workplaceType is
    blank or hybrid."""
    from applypilot.discovery.ashby import _location_string
    posting = {"location": "Seattle, WA", "isRemote": True}
    assert "remote" in _location_string(posting).lower()


def test_location_string_skips_onsite_label():
    from applypilot.discovery.ashby import _location_string
    posting = {"location": "Seattle, WA", "workplaceType": "On-site"}
    assert _location_string(posting) == "Seattle, WA"


def test_location_string_dedupes_secondary():
    from applypilot.discovery.ashby import _location_string
    posting = {
        "location": "Seattle",
        "secondaryLocations": ["Seattle", "Bellevue"],
    }
    out = _location_string(posting)
    assert out.count("Seattle") == 1
    assert "Bellevue" in out


def test_location_string_handles_secondary_dict_form():
    """secondaryLocations can be a list of strings OR a list of {location: ...}
    dicts depending on the employer's setup. Both should work."""
    from applypilot.discovery.ashby import _location_string
    posting = {
        "location": "Seattle",
        "secondaryLocations": [{"location": "New York"}, {"location": "Seattle"}],
    }
    out = _location_string(posting)
    assert "Seattle" in out
    assert "New York" in out
    # Don't double-count Seattle
    assert out.count("Seattle") == 1


# ── End-to-end with mocked HTTP fetch ──────────────────────────────────────

@pytest.fixture
def mock_ashby_api(monkeypatch):
    """Patch the shared fetch helper to return canned data."""
    from applypilot.discovery import ats_common
    captured = {"url": None}

    def fake_fetch(url, timeout=20.0):
        captured["url"] = url
        return {
            "apiVersion": 1,
            "jobs": [
                {
                    "id": "cb12cd4d",
                    "title": "Senior Software Engineer",
                    "department": "Engineering",
                    "location": "Seattle",
                    "secondaryLocations": [],
                    "publishedAt": "2026-04-01T12:00:00.000+00:00",
                    "isListed": True,
                    "isRemote": False,
                    "workplaceType": "Hybrid",
                    "jobUrl": "https://jobs.ashbyhq.com/acme/cb12cd4d",
                    "applyUrl": "https://jobs.ashbyhq.com/acme/cb12cd4d/application",
                    "descriptionPlain": "About us...\n\nWe build things.",
                    "descriptionHtml": "<p>...</p>",
                },
                {
                    "id": "tokyo-job",
                    "title": "Sales in Tokyo",
                    "location": "Tokyo, Japan",
                    "secondaryLocations": [],
                    "isListed": True,
                    "workplaceType": "On-site",
                    "jobUrl": "https://jobs.ashbyhq.com/acme/tokyo-job",
                    "applyUrl": "https://jobs.ashbyhq.com/acme/tokyo-job/application",
                    "descriptionPlain": "Sales role in Tokyo.",
                },
                {
                    "id": "draft-job",
                    "title": "Internal Draft",
                    "location": "Seattle",
                    "isListed": False,  # Should be filtered out
                    "jobUrl": "https://jobs.ashbyhq.com/acme/draft-job",
                    "descriptionPlain": "Not yet published",
                },
            ],
        }

    monkeypatch.setattr(ats_common, "_fetch_json", fake_fetch)
    return captured


def test_scrape_one_employer_filters_unlisted_jobs(mock_ashby_api):
    """isListed=False jobs should be skipped (drafts, internal, etc.)."""
    from applypilot.discovery.ashby import scrape_one_employer
    jobs, err = scrape_one_employer(
        "acme", {"name": "Acme"},
        accept_locs=["seattle", "remote", "wa"],
    )
    assert err is None
    titles = [j["title"] for j in jobs]
    assert "Internal Draft" not in titles
    assert "Sales in Tokyo" not in titles  # location filter
    assert "Senior Software Engineer" in titles


def test_scrape_one_employer_normalizes_fields(mock_ashby_api):
    from applypilot.discovery.ashby import scrape_one_employer
    jobs, err = scrape_one_employer("acme", {"name": "Acme"},
                                    accept_locs=["seattle", "remote", "wa"])
    j = jobs[0]
    assert j["title"] == "Senior Software Engineer"
    assert j["url"] == "https://jobs.ashbyhq.com/acme/cb12cd4d"
    assert j["application_url"] == "https://jobs.ashbyhq.com/acme/cb12cd4d/application"
    assert j["posted_at"] == "2026-04-01T12:00:00.000+00:00"
    assert "Seattle" in j["location"]


def test_scrape_one_employer_hits_correct_endpoint(mock_ashby_api):
    from applypilot.discovery.ashby import scrape_one_employer
    scrape_one_employer("motherduck", {"name": "MotherDuck"}, accept_locs=[])
    assert mock_ashby_api["url"] == "https://api.ashbyhq.com/posting-api/job-board/motherduck"


def test_load_employers_reads_yaml():
    from applypilot.discovery.ashby import load_employers
    employers = load_employers()
    # Bootstrap registry has at least these verified slugs
    for slug in ("motherduck", "statsig", "deepgram", "commonroom"):
        assert slug in employers, f"missing {slug} in registry"
