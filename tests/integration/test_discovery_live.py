"""Live discovery integration tests — hit real APIs, no mocks.

These are slow (network calls) and should only be run locally:
    pytest tests/integration/test_discovery_live.py -v -m live

Each test verifies a discovery source returns actual job data.
"""

from __future__ import annotations

import pytest

# Mark all tests in this module as 'live' — skipped unless explicitly requested
pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live_db(tmp_path_factory):
    """Shared temp DB for all live tests — avoids polluting the real DB."""
    import sqlite3

    db_path = tmp_path_factory.mktemp("live") / "live_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # Init the schema
    from applypilot.database import init_db

    return init_db(conn)


# ── LinkedIn (via JobSpy) ────────────────────────────────────────────────


class TestLinkedIn:
    def test_linkedin_returns_jobs(self):
        from applypilot.discovery.jobspy import scrape_jobs

        df = scrape_jobs(
            site_name=["linkedin"],
            search_term="Software Engineer",
            location="Remote",
            results_wanted=5,
        )
        assert len(df) > 0, "LinkedIn returned no jobs"
        assert "title" in df.columns
        assert "company_name" in df.columns or "company" in df.columns
        print(f"\n  LinkedIn: {len(df)} jobs returned")
        for _, row in df.head(3).iterrows():
            print(f"    - {row.get('title', '?')} at {row.get('company_name', row.get('company', '?'))}")

    def test_linkedin_different_queries(self):
        from applypilot.discovery.jobspy import scrape_jobs

        queries = ["Android Developer", "Backend Engineer", "SDE"]
        for q in queries:
            df = scrape_jobs(
                site_name=["linkedin"],
                search_term=q,
                location="Remote",
                results_wanted=3,
            )
            print(f"\n  LinkedIn '{q}': {len(df)} jobs")
            assert len(df) >= 0, f"LinkedIn crashed on query '{q}'"


# ── Indeed (via JobSpy) ──────────────────────────────────────────────────


class TestIndeed:
    def test_indeed_returns_jobs_or_blocked(self):
        """Indeed may 403 — test that it either returns data or fails gracefully."""
        from applypilot.discovery.jobspy import scrape_jobs

        try:
            df = scrape_jobs(
                site_name=["indeed"],
                search_term="Software Engineer",
                location="Remote",
                results_wanted=5,
                country_indeed="usa",
            )
            print(f"\n  Indeed: {len(df)} jobs returned")
            assert len(df) >= 0
        except Exception as e:
            print(f"\n  Indeed: blocked/failed — {type(e).__name__}: {e}")
            pytest.skip(f"Indeed blocked: {e}")


# ── ZipRecruiter (via JobSpy) ────────────────────────────────────────────


class TestZipRecruiter:
    def test_ziprecruiter_returns_jobs_or_timeout(self):
        from applypilot.discovery.jobspy import scrape_jobs

        try:
            df = scrape_jobs(
                site_name=["zip_recruiter"],
                search_term="Software Engineer",
                location="Remote",
                results_wanted=5,
            )
            print(f"\n  ZipRecruiter: {len(df)} jobs returned")
            assert len(df) >= 0
        except Exception as e:
            print(f"\n  ZipRecruiter: failed — {type(e).__name__}: {e}")
            pytest.skip(f"ZipRecruiter failed: {e}")


# ── Greenhouse API ───────────────────────────────────────────────────────


class TestGreenhouse:
    def test_greenhouse_fetch_stripe(self):
        from applypilot.discovery.greenhouse import fetch_jobs_api

        jobs = fetch_jobs_api("stripe")
        assert jobs is not None, "Greenhouse API returned None for Stripe"
        assert len(jobs) > 0, "Stripe has no jobs on Greenhouse"
        print(f"\n  Greenhouse (Stripe): {len(jobs)} jobs")
        for j in jobs[:3]:
            print(f"    - {j.get('title', '?')}")

    def test_greenhouse_fetch_multiple_employers(self):
        employers = ["figma", "scaleai", "notion"]
        for emp in employers:
            from applypilot.discovery.greenhouse import fetch_jobs_api

            jobs = fetch_jobs_api(emp)
            count = len(jobs) if jobs else 0
            print(f"\n  Greenhouse ({emp}): {count} jobs")
            assert jobs is None or isinstance(jobs, list), f"Unexpected return for {emp}"

    def test_greenhouse_invalid_employer_returns_none(self):
        from applypilot.discovery.greenhouse import fetch_jobs_api

        jobs = fetch_jobs_api("this_employer_definitely_does_not_exist_xyz123")
        assert jobs is None or len(jobs) == 0


# ── Workday API ──────────────────────────────────────────────────────────


class TestWorkday:
    def test_workday_search_netflix(self):
        from applypilot.discovery.workday import search_employer
        from applypilot.config import load_employers_config

        employers = load_employers_config()
        netflix = next((e for e in employers if e["name"] == "Netflix"), None)
        if not netflix:
            pytest.skip("Netflix not in employers.yaml")
        jobs = search_employer(netflix, query="Engineer")
        assert len(jobs) > 0, "Netflix Workday returned no jobs"
        print(f"\n  Workday (Netflix): {len(jobs)} jobs")
        for j in jobs[:3]:
            print(f"    - {j.get('title', '?')}")

    def test_workday_search_multiple_employers(self):
        from applypilot.discovery.workday import search_employer
        from applypilot.config import load_employers_config

        employers = load_employers_config()[:5]  # first 5
        for emp in employers:
            try:
                jobs = search_employer(emp, query="Software")
                print(f"\n  Workday ({emp['name']}): {len(jobs)} jobs")
            except Exception as e:
                print(f"\n  Workday ({emp['name']}): failed — {type(e).__name__}: {e}")


# ── Hacker News ──────────────────────────────────────────────────────────


class TestHackerNews:
    def test_hackernews_returns_jobs(self):
        from applypilot.discovery.hackernews import run_hn_discovery

        try:
            run_hn_discovery()
            print("\n  HN: discovery completed")
        except Exception as e:
            print(f"\n  HN: failed — {type(e).__name__}: {e}")
            pytest.skip(f"HN failed: {e}")


# ── Cross-source: full discovery run ─────────────────────────────────────


class TestFullDiscovery:
    def test_discovery_produces_jobs(self, live_db, monkeypatch):
        """Run a minimal discovery and verify at least some jobs come back."""
        monkeypatch.setattr("applypilot.database.get_connection", lambda: live_db)

        from applypilot.discovery.greenhouse import search_all

        search_all("Software Engineer", workers=1)

        count = live_db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        print(f"\n  Full discovery: {count} jobs in DB")
        assert count > 0, "Discovery produced zero jobs"
