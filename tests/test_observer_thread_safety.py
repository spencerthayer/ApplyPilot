"""Tests for AnalyticsObserver thread safety — SQLite threading bug regression."""

import sqlite3
import threading
import time

import pytest

from applypilot.analytics.observer import AnalyticsObserver
from applypilot.db.dto import AnalyticsEventDTO
from applypilot.db.schema import init_db
from applypilot.db.sqlite.analytics_repo import SqliteAnalyticsRepository


class TestObserverThreadSafety:
    """Regression: observer daemon thread must not reuse the main thread's SQLite connection.

    Previously, the observer called self._repo (created on main thread) from a
    daemon thread, causing:
        sqlite3.ProgrammingError: SQLite objects created in a thread can only
        be used in that same thread.
    """

    @pytest.fixture
    def main_conn(self, tmp_path):
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db), check_same_thread=True)
        conn.row_factory = sqlite3.Row
        init_db(conn)
        return conn

    def test_observer_does_not_crash_on_background_thread(self, main_conn):
        """Observer should create its own connection in the background thread."""
        repo = SqliteAnalyticsRepository(main_conn)
        observer = AnalyticsObserver(repo)

        # Insert an event from the main thread
        main_conn.execute(
            "INSERT INTO analytics_events (event_id, timestamp, stage, event_type, payload) VALUES (?, ?, ?, ?, ?)",
            (
                "evt1",
                "2026-01-01T00:00:00Z",
                "score",
                "job_scored",
                '{"fit_score": 8, "matched_skills": ["Python"], "missing_requirements": ["Go"], "site": "indeed"}',
            ),
        )
        main_conn.commit()

        # Start observer — it runs _process_batch in a daemon thread
        errors = []

        def _run_with_error_capture():
            try:
                observer._run()
            except Exception as e:
                errors.append(e)

        observer._stop = threading.Event()
        t = threading.Thread(target=_run_with_error_capture, daemon=True)
        t.start()

        # Let it run one cycle
        time.sleep(2)
        observer._stop.set()
        t.join(timeout=3)

        # Should NOT have raised ProgrammingError
        sqlite_errors = [e for e in errors if "thread" in str(e).lower()]
        assert sqlite_errors == [], f"SQLite threading error: {sqlite_errors}"

    def test_observer_processes_events_from_thread(self, main_conn, tmp_path, monkeypatch):
        """Observer should process events without SQLite threading errors."""
        db_path = str(tmp_path / "test.db")
        monkeypatch.setattr(
            "applypilot.db.sqlite.connection.get_connection",
            lambda *a, **kw: _make_conn(db_path),
        )

        repo = SqliteAnalyticsRepository(main_conn)
        observer = AnalyticsObserver(repo)

        # Process directly (not in thread) to verify logic works
        main_conn.execute(
            "INSERT INTO analytics_events (event_id, timestamp, stage, event_type, payload) VALUES (?, ?, ?, ?, ?)",
            (
                "evt2",
                "2026-01-01T00:00:00Z",
                "score",
                "job_scored",
                '{"fit_score": 9, "matched_skills": ["Kotlin"], "missing_requirements": ["Rust"], "site": "linkedin"}',
            ),
        )
        main_conn.commit()

        # Call _process_batch directly with the main-thread repo (tests logic, not threading)
        observer._thread_repo = repo
        observer._process_batch()

        assert observer.skill_gaps.total_jobs_analyzed == 1
        assert observer.pool.total == 1


def _make_conn(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn
