from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd

from applypilot.discovery import jobspy


def test_normalize_scrape_kwargs_adapts_old_jobspy_signature(monkeypatch) -> None:
    monkeypatch.setattr(
        jobspy,
        "_SCRAPE_JOBS_PARAMS",
        {"site_name", "search_term", "location", "results_wanted", "proxy", "is_remote", "country_indeed"},
    )
    monkeypatch.setattr(jobspy, "_HOURS_OLD_SUPPORTED", False)
    monkeypatch.setattr(jobspy, "_HOURS_OLD_WARNING_EMITTED", False)

    normalized = jobspy._normalize_scrape_kwargs(
        {
            "site_name": ["linkedin"],
            "search_term": "Backend Engineer",
            "location": "Remote",
            "results_wanted": 50,
            "hours_old": 72,
            "description_format": "markdown",
            "verbose": 0,
            "linkedin_fetch_description": True,
            "proxies": ["user:pass@host:1234"],
            "is_remote": True,
        }
    )

    assert normalized == {
        "site_name": ["linkedin"],
        "search_term": "Backend Engineer",
        "location": "Remote",
        "results_wanted": 50,
        "proxy": "user:pass@host:1234",
        "is_remote": True,
    }
    assert jobspy._HOURS_OLD_WARNING_EMITTED is True


def test_scrape_with_retry_uses_normalized_kwargs(monkeypatch) -> None:
    monkeypatch.setattr(jobspy, "_SCRAPE_JOBS_PARAMS", {"site_name", "search_term", "location", "proxy"})
    monkeypatch.setattr(jobspy, "_HOURS_OLD_SUPPORTED", False)

    captured = {}

    def _fake_scrape_jobs(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(jobspy, "scrape_jobs", _fake_scrape_jobs)

    result = jobspy._scrape_with_retry(
        {
            "site_name": ["indeed"],
            "search_term": "Systems Architect",
            "location": "Remote",
            "hours_old": 72,
            "proxies": ["proxy.example:443"],
        },
        max_retries=0,
    )

    assert isinstance(result, SimpleNamespace)
    assert captured == {
        "site_name": ["indeed"],
        "search_term": "Systems Architect",
        "location": "Remote",
        "proxy": "proxy.example:443",
    }


def test_apply_local_hours_filter_uses_date_posted_when_available() -> None:
    now = datetime.now(timezone.utc)
    df = pd.DataFrame(
        [
            {"title": "fresh", "date_posted": now.date()},
            {"title": "stale", "date_posted": (now - timedelta(days=10)).date()},
            {"title": "unknown", "date_posted": None},
        ]
    )

    filtered, removed, enforced = jobspy._apply_local_hours_filter(df, hours_old=72)

    assert enforced is True
    assert removed == 1
    assert list(filtered["title"]) == ["fresh", "unknown"]


def test_apply_local_hours_filter_is_noop_without_supported_column() -> None:
    df = pd.DataFrame([{"title": "job", "location": "Remote"}])

    filtered, removed, enforced = jobspy._apply_local_hours_filter(df, hours_old=72)

    assert filtered.equals(df)
    assert removed == 0
    assert enforced is False
