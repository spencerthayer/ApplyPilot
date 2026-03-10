from __future__ import annotations

import urllib.error

from applypilot.discovery import workday


def test_search_employer_raises_quarantine_failure_for_first_page_422(monkeypatch) -> None:
    employer = {
        "name": "Bad Employer",
        "base_url": "https://example.wd1.myworkdayjobs.com",
        "tenant": "example",
        "site_id": "careers",
    }

    def _raise_http_error(*args, **kwargs):
        raise urllib.error.HTTPError(
            employer["base_url"],
            422,
            "Unprocessable Entity",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(workday, "workday_search", _raise_http_error)

    try:
        workday.search_employer("bad", employer, "Systems Architect")
    except workday.WorkdayEmployerFailure as exc:
        assert exc.quarantine is True
        assert "HTTP Error 422" in str(exc)
    else:
        raise AssertionError("Expected WorkdayEmployerFailure")


def test_run_workday_discovery_skips_quarantined_employers_on_later_queries(monkeypatch) -> None:
    employers = {
        "bad": {"name": "Bad Employer"},
        "good": {"name": "Good Employer"},
    }
    calls: list[list[str]] = []

    monkeypatch.setattr(
        workday.config,
        "load_search_config",
        lambda: {
            "queries": [
                {"query": "Systems Architect", "tier": 1},
                {"query": "Senior Full Stack Developer", "tier": 1},
            ]
        },
    )

    def _fake_scrape_employers(*, employer_keys=None, **kwargs):
        calls.append(list(employer_keys or []))
        if len(calls) == 1:
            return {
                "found": 0,
                "new": 0,
                "existing": 0,
                "errors": 1,
                "quarantined": {"bad"},
            }
        return {
            "found": 1,
            "new": 1,
            "existing": 0,
            "errors": 0,
            "quarantined": set(),
        }

    monkeypatch.setattr(workday, "scrape_employers", _fake_scrape_employers)

    result = workday.run_workday_discovery(employers=employers, workers=1)

    assert calls == [["bad", "good"], ["good"]]
    assert result["errors"] == 1
    assert result["new"] == 1
