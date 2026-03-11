from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

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


def test_run_one_search_remote_drops_indeed_and_runs_each_site(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    calls: list[list[str]] = []

    def _fake_scrape(kwargs: dict, max_retries: int = 0):
        calls.append(kwargs["site_name"])
        site = kwargs["site_name"][0]
        return pd.DataFrame([{"job_url": f"https://example.com/{site}", "location": "Remote", "site": site}])

    def _fake_zip(**kwargs):
        calls.append(["zip_recruiter"])
        return pd.DataFrame([{"job_url": "https://example.com/zip", "location": "Remote", "site": "zip_recruiter"}])

    monkeypatch.setattr(jobspy, "_JOBSPY_SITE_QUARANTINE_PATH", tmp_path / "jobspy_site_quarantine.json")
    monkeypatch.setattr(jobspy, "_scrape_with_retry", _fake_scrape)
    monkeypatch.setattr(jobspy, "_scrape_ziprecruiter_browser", _fake_zip)
    monkeypatch.setattr(jobspy, "get_connection", lambda: object())
    monkeypatch.setattr(jobspy, "store_jobspy_results", lambda conn, df, source_label: (len(df), 0))

    result = jobspy._run_one_search(
        search={"query": "Backend Engineer", "location": "Remote", "remote": True, "tier": 1},
        sites=["indeed", "linkedin", "zip_recruiter"],
        results_per_site=50,
        hours_old=72,
        proxy_config=None,
        defaults={},
        max_retries=0,
        accept_locs=[],
        reject_locs=[],
        glassdoor_map={},
    )

    assert calls == [["linkedin"], ["zip_recruiter"]]
    assert result["errors"] == 0
    assert result["total"] == 2


def test_run_one_search_keeps_partial_success_when_one_site_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    def _fake_scrape(kwargs: dict, max_retries: int = 0):
        site = kwargs["site_name"][0]
        return pd.DataFrame([{"job_url": "https://example.com/li", "location": "Remote", "site": site}])

    def _fake_zip(**kwargs):
        raise RuntimeError("bad response status code: 403")

    monkeypatch.setattr(jobspy, "_JOBSPY_SITE_QUARANTINE_PATH", tmp_path / "jobspy_site_quarantine.json")
    monkeypatch.setattr(jobspy, "_scrape_with_retry", _fake_scrape)
    monkeypatch.setattr(jobspy, "_scrape_ziprecruiter_browser", _fake_zip)
    monkeypatch.setattr(jobspy, "get_connection", lambda: object())
    monkeypatch.setattr(jobspy, "store_jobspy_results", lambda conn, df, source_label: (len(df), 0))

    result = jobspy._run_one_search(
        search={"query": "Systems Architect", "location": "Remote", "remote": True, "tier": 1},
        sites=["linkedin", "zip_recruiter"],
        results_per_site=50,
        hours_old=72,
        proxy_config=None,
        defaults={},
        max_retries=0,
        accept_locs=[],
        reject_locs=[],
        glassdoor_map={},
    )

    assert result["errors"] == 0
    assert result["new"] == 1
    assert "all sites failed" not in caplog.text
    assert "(site=zip_recruiter)" in caplog.text
    assert "partial site success" in caplog.text


def test_run_one_search_reports_error_when_all_sites_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    def _fake_scrape(kwargs: dict, max_retries: int = 0):
        site = kwargs["site_name"][0]
        raise RuntimeError(f"{site} blocked 403")

    def _fake_zip(**kwargs):
        raise RuntimeError("zip_recruiter blocked 403")

    monkeypatch.setattr(jobspy, "_JOBSPY_SITE_QUARANTINE_PATH", tmp_path / "jobspy_site_quarantine.json")
    monkeypatch.setattr(jobspy, "_scrape_with_retry", _fake_scrape)
    monkeypatch.setattr(jobspy, "_scrape_ziprecruiter_browser", _fake_zip)

    result = jobspy._run_one_search(
        search={"query": "UI/UX", "location": "Remote", "remote": True, "tier": 3},
        sites=["linkedin", "zip_recruiter"],
        results_per_site=50,
        hours_old=72,
        proxy_config=None,
        defaults={},
        max_retries=0,
        accept_locs=[],
        reject_locs=[],
        glassdoor_map={},
    )

    assert result["errors"] == 1
    assert "all sites failed" in caplog.text
    assert "(site=linkedin)" in caplog.text
    assert "(site=zip_recruiter)" in caplog.text


def test_search_jobs_keeps_partial_success_when_one_site_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    def _fake_scrape(kwargs: dict, max_retries: int = 0):
        site = kwargs["site_name"][0]
        return pd.DataFrame([{"job_url": "https://example.com/li", "location": "Remote", "site": site}])

    def _fake_zip(**kwargs):
        raise RuntimeError("bad response status code: 403")

    class _FakeConn:
        def execute(self, _query: str):
            return SimpleNamespace(fetchone=lambda: [1])

    monkeypatch.setattr(jobspy, "_JOBSPY_SITE_QUARANTINE_PATH", tmp_path / "jobspy_site_quarantine.json")
    monkeypatch.setattr(jobspy, "_scrape_with_retry", _fake_scrape)
    monkeypatch.setattr(jobspy, "_scrape_ziprecruiter_browser", _fake_zip)
    monkeypatch.setattr(jobspy, "init_db", lambda: _FakeConn())
    monkeypatch.setattr(jobspy, "store_jobspy_results", lambda conn, df, source_label: (len(df), 0))

    result = jobspy.search_jobs(
        query="Systems Architect",
        location="Remote",
        sites=["linkedin", "zip_recruiter"],
        remote_only=True,
    )

    assert result["total"] == 1
    assert result["new"] == 1
    assert "all sites failed" not in caplog.text
    assert "partial site success" in caplog.text


def test_full_crawl_quarantines_ziprecruiter_after_first_403(
    monkeypatch: pytest.MonkeyPatch, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[tuple[str, str]] = []

    def _fake_scrape(kwargs: dict, max_retries: int = 0):
        site = kwargs["site_name"][0]
        query = kwargs["search_term"]
        calls.append((site, query))
        return pd.DataFrame([{"job_url": f"https://example.com/{query}", "location": "Remote", "site": site}])

    def _fake_zip(**kwargs):
        calls.append(("zip_recruiter", kwargs["search_term"]))
        raise RuntimeError("bad response status code: 403")

    fake_conn = SimpleNamespace(execute=lambda _query: SimpleNamespace(fetchone=lambda: [5]))

    monkeypatch.setattr(jobspy, "_JOBSPY_SITE_QUARANTINE_PATH", tmp_path / "jobspy_site_quarantine.json")
    monkeypatch.setattr(jobspy, "_scrape_with_retry", _fake_scrape)
    monkeypatch.setattr(jobspy, "_scrape_ziprecruiter_browser", _fake_zip)
    monkeypatch.setattr(jobspy, "init_db", lambda: None)
    monkeypatch.setattr(jobspy, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(jobspy, "store_jobspy_results", lambda conn, df, source_label: (len(df), 0))

    result = jobspy._full_crawl(
        search_cfg={
            "queries": [{"query": "Systems Architect"}, {"query": "Backend Engineer"}],
            "locations": [{"location": "Remote", "remote": True}],
            "sites": ["linkedin", "zip_recruiter"],
            "defaults": {},
        },
        sites=["linkedin", "zip_recruiter"],
        results_per_site=50,
        hours_old=72,
        max_retries=0,
    )

    assert calls == [
        ("linkedin", "Systems Architect"),
        ("zip_recruiter", "Systems Architect"),
        ("linkedin", "Backend Engineer"),
    ]
    assert result["errors"] == 0
    assert "quarantined until" in caplog.text
    assert (tmp_path / "jobspy_site_quarantine.json").exists()


def test_search_jobs_skips_preexisting_quarantined_ziprecruiter(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    quarantine_path = tmp_path / "jobspy_site_quarantine.json"
    quarantine_path.write_text(
        '{"zip_recruiter": {"until": "2999-01-01T00:00:00+00:00", "reason": "cloudflare_403"}}',
        encoding="utf-8",
    )

    calls: list[dict] = []

    def _fake_scrape(kwargs: dict, max_retries: int = 0):
        calls.append(kwargs)
        return pd.DataFrame()

    monkeypatch.setattr(jobspy, "_JOBSPY_SITE_QUARANTINE_PATH", quarantine_path)
    monkeypatch.setattr(jobspy, "_scrape_with_retry", _fake_scrape)
    monkeypatch.setattr(jobspy, "_scrape_ziprecruiter_browser", lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not run")))

    result = jobspy.search_jobs(
        query="Systems Architect",
        location="Remote",
        sites=["zip_recruiter"],
        remote_only=True,
    )

    assert result == {"total": 0, "new": 0, "existing": 0}
    assert calls == []


def test_merge_ziprecruiter_page_data_prefers_itemlist_urls() -> None:
    merged = jobspy._merge_ziprecruiter_page_data(
        item_list=[
            {"name": "Systems Architect", "url": "https://www.ziprecruiter.com/c/Foo/Job/Systems-Architect?jid=123"},
            {"name": "Backend Engineer", "url": "https://www.ziprecruiter.com/c/Bar/Job/Backend-Engineer?jid=456"},
        ],
        cards=[
            {"title": "Systems Architect", "company": "Foo", "location": "Remote, US · Remote", "salary": "$100K/yr"},
            {"title": "Backend Engineer", "company": "Bar", "location": "Austin, TX · On-site +1", "salary": ""},
        ],
    )

    assert merged[0]["job_url"].endswith("jid=123")
    assert merged[0]["is_remote"] is True
    assert merged[1]["title"] == "Backend Engineer"
    assert merged[1]["salary"] is None
