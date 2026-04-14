"""Tests for core database operations (V1 — uses repo layer).

Covers:
- migrate_from_dto: adds missing columns, never drops existing ones
- resolve_url: absolute pass-through, relative resolution, missing base
- job_repo.upsert: deduplication by URL
- categorize_apply_result: correct bucketing of status + error combinations
- write_with_retry: commits successfully without lock contention
"""

from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Create a minimal table first (simulates pre-V1 DB)
    conn.execute(
        "CREATE TABLE jobs ("
        "  url TEXT PRIMARY KEY, title TEXT, salary TEXT, description TEXT,"
        "  location TEXT, site TEXT, strategy TEXT, discovered_at TEXT,"
        "  fit_score REAL, score_error TEXT, apply_status TEXT"
        ")"
    )
    conn.commit()
    return conn


class TestMigrateFromDto(unittest.TestCase):
    """migrate_from_dto adds missing columns without touching existing ones."""

    def test_adds_missing_columns(self):
        conn = _make_db()
        from applypilot.db.schema import migrate_from_dto

        added = migrate_from_dto(conn)

        existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        self.assertIn("pipeline_status", existing)
        self.assertIn("full_description", existing)
        self.assertGreater(len(added), 0)

    def test_no_duplicate_adds(self):
        conn = _make_db()
        from applypilot.db.schema import migrate_from_dto

        migrate_from_dto(conn)
        second = migrate_from_dto(conn)
        self.assertEqual(second, [])

    def test_existing_rows_preserved(self):
        conn = _make_db()
        conn.execute("INSERT INTO jobs (url, title) VALUES (?, ?)", ("https://example.com/1", "Engineer"))
        conn.commit()

        from applypilot.db.schema import migrate_from_dto

        migrate_from_dto(conn)

        row = conn.execute("SELECT title FROM jobs WHERE url = ?", ("https://example.com/1",)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "Engineer")


class TestResolveUrl(unittest.TestCase):
    """resolve_url handles absolute URLs, relative paths, and missing bases."""

    def setUp(self):
        self._patcher = patch(
            "applypilot.config.load_base_urls",
            return_value={"remoteok": "https://remoteok.com"},
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_absolute_url_unchanged(self):
        from applypilot.enrichment.url_resolver import resolve_url

        self.assertEqual(resolve_url("https://jobs.example.com/123", "anySite"), "https://jobs.example.com/123")

    def test_relative_url_resolved(self):
        from applypilot.enrichment.url_resolver import resolve_url

        self.assertEqual(resolve_url("/jobs/swe-123", "remoteok"), "https://remoteok.com/jobs/swe-123")

    def test_relative_url_no_base_returns_none(self):
        from applypilot.enrichment.url_resolver import resolve_url

        self.assertIsNone(resolve_url("/jobs/swe-123", "unknownsite"))

    def test_empty_url_returns_none(self):
        from applypilot.enrichment.url_resolver import resolve_url

        self.assertIsNone(resolve_url("", "remoteok"))


class TestStoreJobs(unittest.TestCase):
    """job_repo.upsert deduplicates by URL."""

    def test_new_job_stored(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        from applypilot.db.schema import init_db
        from applypilot.db.sqlite.job_repo import SqliteJobRepository
        from applypilot.db.dto import JobDTO

        init_db(conn)
        repo = SqliteJobRepository(conn)

        repo.upsert(JobDTO(url="https://example.com/1", title="SWE", site="test"))
        job = repo.get_by_url("https://example.com/1")
        self.assertIsNotNone(job)
        self.assertEqual(job.title, "SWE")

    def test_duplicate_url_skipped(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        from applypilot.db.schema import init_db
        from applypilot.db.sqlite.job_repo import SqliteJobRepository
        from applypilot.db.dto import JobDTO

        init_db(conn)
        repo = SqliteJobRepository(conn)

        repo.upsert(JobDTO(url="https://example.com/1", title="SWE", site="test"))
        repo.upsert(JobDTO(url="https://example.com/1", title="SWE v2", site="test"))
        job = repo.get_by_url("https://example.com/1")
        self.assertEqual(job.title, "SWE v2")  # INSERT OR REPLACE

    def test_relative_url_resolved_before_store(self):
        """URL resolution is handled by the enrichment layer, not the repo."""
        pass  # URL resolution is now in enrichment/url_resolver.py

    def test_unresolvable_relative_url_skipped(self):
        """URL resolution is handled by the enrichment layer, not the repo."""
        pass


class TestCategorizeApplyResult(unittest.TestCase):
    """categorize_apply_result returns the correct semantic category."""

    def test_none_status_is_pending(self):
        from applypilot.apply.categorizer import categorize_apply_result

        self.assertEqual(categorize_apply_result(None, None), "pending")

    def test_applied_status(self):
        from applypilot.apply.categorizer import categorize_apply_result

        self.assertEqual(categorize_apply_result("applied", None), "applied")

    def test_needs_human_status(self):
        from applypilot.apply.categorizer import categorize_apply_result

        self.assertEqual(categorize_apply_result("needs_human", None), "needs_human")

    def test_auth_error_is_blocked_auth(self):
        from applypilot.apply.categorizer import categorize_apply_result

        self.assertEqual(categorize_apply_result("failed", "login_required"), "blocked_auth")
        self.assertEqual(categorize_apply_result("failed", "sso_required"), "blocked_auth")

    def test_ineligible_error_is_archived(self):
        from applypilot.apply.categorizer import categorize_apply_result

        self.assertEqual(categorize_apply_result("failed", "not_eligible_location"), "archived_ineligible")

    def test_unknown_error_is_blocked_technical(self):
        from applypilot.apply.categorizer import categorize_apply_result

        self.assertEqual(categorize_apply_result("failed", "some_new_error_code"), "blocked_technical")


class TestWriteWithRetry(unittest.TestCase):
    """write_with_retry commits successfully on first attempt."""

    def test_successful_write(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        from applypilot.db.schema import init_db
        from applypilot.db.sqlite.write_retry import write_with_retry

        init_db(conn)

        def do_insert():
            conn.execute("INSERT INTO jobs (url, title) VALUES (?, ?)", ("https://retry.example.com/1", "Retry Test"))

        write_with_retry(conn, do_insert)

        row = conn.execute("SELECT title FROM jobs WHERE url = ?", ("https://retry.example.com/1",)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "Retry Test")


if __name__ == "__main__":
    unittest.main(verbosity=2)
