"""Tests for core database operations.

Covers:
- ensure_columns: adds missing columns, never drops existing ones
- _resolve_url: absolute pass-through, relative resolution, missing base
- store_jobs: deduplication by URL, skips unresolvable relative URLs
- categorize_apply_result: correct bucketing of status + error combinations
- write_with_retry: commits successfully without lock contention
"""

import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _make_db() -> sqlite3.Connection:
    """Return an in-memory SQLite connection with a minimal jobs table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE jobs ("
        "  url TEXT PRIMARY KEY,"
        "  title TEXT,"
        "  salary TEXT,"
        "  description TEXT,"
        "  location TEXT,"
        "  site TEXT,"
        "  strategy TEXT,"
        "  discovered_at TEXT,"
        "  fit_score REAL,"
        "  score_error TEXT,"
        "  apply_status TEXT"
        ")"
    )
    conn.commit()
    return conn


class TestEnsureColumns(unittest.TestCase):
    """ensure_columns adds missing columns without touching existing ones."""

    def test_adds_missing_columns(self):
        conn = _make_db()
        from applypilot.database import _ALL_COLUMNS, ensure_columns

        added = ensure_columns(conn)

        # Every column in the registry should now exist in the table
        existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        for col in _ALL_COLUMNS:
            if "PRIMARY KEY" not in _ALL_COLUMNS[col]:
                self.assertIn(col, existing, f"Column '{col}' missing after ensure_columns")

        self.assertGreater(len(added), 0, "Should have added at least one missing column")

    def test_no_duplicate_adds(self):
        conn = _make_db()
        from applypilot.database import ensure_columns

        ensure_columns(conn)
        second = ensure_columns(conn)
        self.assertEqual(second, [], "Second call should add nothing")

    def test_existing_rows_preserved(self):
        conn = _make_db()
        conn.execute(
            "INSERT INTO jobs (url, title) VALUES (?, ?)",
            ("https://example.com/job/1", "Engineer"),
        )
        conn.commit()

        from applypilot.database import ensure_columns
        ensure_columns(conn)

        row = conn.execute(
            "SELECT title FROM jobs WHERE url = ?", ("https://example.com/job/1",)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "Engineer")


class TestResolveUrl(unittest.TestCase):
    """_resolve_url handles absolute URLs, relative paths, and missing bases."""

    def setUp(self):
        self._patcher = patch(
            "applypilot.config.load_base_urls",
            return_value={"remoteok": "https://remoteok.com"},
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_absolute_url_unchanged(self):
        from applypilot.database import _resolve_url
        url = "https://jobs.example.com/position/123"
        self.assertEqual(_resolve_url(url, "anySite"), url)

    def test_relative_url_resolved(self):
        from applypilot.database import _resolve_url
        result = _resolve_url("/jobs/swe-123", "remoteok")
        self.assertEqual(result, "https://remoteok.com/jobs/swe-123")

    def test_relative_url_no_base_returns_none(self):
        from applypilot.database import _resolve_url
        result = _resolve_url("/jobs/swe-123", "unknownsite")
        self.assertIsNone(result)

    def test_empty_url_returns_none(self):
        from applypilot.database import _resolve_url
        self.assertIsNone(_resolve_url("", "remoteok"))
        self.assertIsNone(_resolve_url(None, "remoteok"))


class TestStoreJobs(unittest.TestCase):
    """store_jobs deduplicates by URL and resolves relative URLs."""

    def setUp(self):
        self._patcher = patch(
            "applypilot.config.load_base_urls",
            return_value={"testsite": "https://testsite.example.com"},
        )
        self._patcher.start()
        self.conn = _make_db()
        from applypilot.database import ensure_columns
        ensure_columns(self.conn)

    def tearDown(self):
        self._patcher.stop()

    def test_new_job_stored(self):
        from applypilot.database import store_jobs
        jobs = [{"url": "https://testsite.example.com/job/1", "title": "SWE"}]
        new, dupes = store_jobs(self.conn, jobs, "testsite", "api")
        self.assertEqual(new, 1)
        self.assertEqual(dupes, 0)

    def test_duplicate_url_skipped(self):
        from applypilot.database import store_jobs
        jobs = [{"url": "https://testsite.example.com/job/1", "title": "SWE"}]
        store_jobs(self.conn, jobs, "testsite", "api")
        new, dupes = store_jobs(self.conn, jobs, "testsite", "api")
        self.assertEqual(new, 0)
        self.assertEqual(dupes, 1)

    def test_relative_url_resolved_before_store(self):
        from applypilot.database import store_jobs
        jobs = [{"url": "/job/99", "title": "Platform Eng"}]
        new, _ = store_jobs(self.conn, jobs, "testsite", "css")
        self.assertEqual(new, 1)

        row = self.conn.execute(
            "SELECT url FROM jobs WHERE title = ?", ("Platform Eng",)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertTrue(row[0].startswith("https://"), f"Expected absolute URL, got: {row[0]}")

    def test_unresolvable_relative_url_skipped(self):
        from applypilot.database import store_jobs
        jobs = [{"url": "/job/orphan", "title": "Orphan Job"}]
        new, dupes = store_jobs(self.conn, jobs, "unknownsite", "css")
        self.assertEqual(new, 0)
        self.assertEqual(dupes, 0)


class TestCategorizeApplyResult(unittest.TestCase):
    """categorize_apply_result returns the correct semantic category."""

    def test_none_status_is_pending(self):
        from applypilot.database import categorize_apply_result
        self.assertEqual(categorize_apply_result(None, None), "pending")

    def test_applied_status(self):
        from applypilot.database import categorize_apply_result
        self.assertEqual(categorize_apply_result("applied", None), "applied")

    def test_needs_human_status(self):
        from applypilot.database import categorize_apply_result
        self.assertEqual(categorize_apply_result("needs_human", None), "needs_human")

    def test_auth_error_is_blocked_auth(self):
        from applypilot.database import categorize_apply_result
        self.assertEqual(
            categorize_apply_result("failed", "login_required"), "blocked_auth"
        )
        self.assertEqual(
            categorize_apply_result("failed", "sso_required"), "blocked_auth"
        )

    def test_ineligible_error_is_archived(self):
        from applypilot.database import categorize_apply_result
        self.assertEqual(
            categorize_apply_result("failed", "not_eligible_location"),
            "archived_ineligible",
        )

    def test_unknown_error_is_blocked_technical(self):
        from applypilot.database import categorize_apply_result
        self.assertEqual(
            categorize_apply_result("failed", "some_new_error_code"),
            "blocked_technical",
        )


class TestWriteWithRetry(unittest.TestCase):
    """write_with_retry commits successfully on first attempt."""

    def test_successful_write(self):
        conn = _make_db()
        from applypilot.database import ensure_columns, write_with_retry
        ensure_columns(conn)

        def do_insert():
            conn.execute(
                "INSERT INTO jobs (url, title) VALUES (?, ?)",
                ("https://retry.example.com/1", "Retry Test"),
            )

        write_with_retry(conn, do_insert)

        row = conn.execute(
            "SELECT title FROM jobs WHERE url = ?",
            ("https://retry.example.com/1",),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "Retry Test")


if __name__ == "__main__":
    unittest.main(verbosity=2)
