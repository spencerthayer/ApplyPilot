"""Tests for comprehensive storage adapter and new CLI commands."""

import sqlite3
import pytest
from applypilot.tailoring.comprehensive_storage import ComprehensiveStorage


class TestComprehensiveStorage:
    @pytest.fixture
    def storage(self):
        return ComprehensiveStorage(db_path=":memory:")

    def test_tables_created(self, storage):
        tables = storage._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        names = {r[0] for r in tables}
        assert "bullets" in names
        assert "bullet_feedback" in names
        assert "evidence" in names
        assert "metrics_registry" in names

    def test_save_and_get_bullet(self, storage):
        from types import SimpleNamespace

        bullet = SimpleNamespace(
            id="b1",
            text="Led team of 12",
            variants={},
            tags=[],
            skills=[],
            domains=[],
            role_families=[],
            evidence_links=[],
            metrics=[],
            vague_claim=False,
            implied_scale=False,
            has_proof=True,
            has_metric=True,
        )
        storage.save_bullet(bullet)
        rows = storage.get_metric_bullets()
        assert len(rows) == 1
        assert rows[0]["id"] == "b1"

    def test_record_feedback(self, storage):
        storage.record_feedback("b1", "SWE", "selected")
        rows = storage._conn.execute("SELECT * FROM bullet_feedback").fetchall()
        assert len(rows) == 1

    def test_save_evidence(self, storage):
        storage.save_evidence("claim", "b1", "[]", "script")
        rows = storage._conn.execute("SELECT * FROM evidence").fetchall()
        assert len(rows) == 1

    def test_save_metric(self, storage):
        storage.save_metric("revenue", "50%", "Q1", "def", "work", "[]")
        rows = storage._conn.execute("SELECT * FROM metrics_registry").fetchall()
        assert len(rows) == 1


class TestMigrationRunner:
    def test_discovers_and_runs(self):
        from applypilot.db.migrations.runner import _discover_migrations, run_pending_migrations

        migrations = _discover_migrations()
        assert 1 in migrations
        assert 2 in migrations
        assert 3 in migrations

    def test_runs_on_fresh_db(self):
        from applypilot.db.migrations.runner import run_pending_migrations

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS jobs ("
            "url TEXT PRIMARY KEY, pipeline_status TEXT, applied_at TEXT, "
            "apply_status TEXT, cover_letter_path TEXT, tailored_resume_path TEXT, "
            "exclusion_reason_code TEXT, fit_score INTEGER, full_description TEXT, "
            "title TEXT, company TEXT, location TEXT, description TEXT)"
        )
        applied = run_pending_migrations(conn)
        assert 1 in applied
        assert 2 in applied
        assert 3 in applied


class TestI18n:
    def test_needs_normalization_ascii(self):
        from applypilot.wizard.i18n import needs_normalization

        assert needs_normalization("Hello world") is False

    def test_needs_normalization_unicode(self):
        from applypilot.wizard.i18n import needs_normalization

        assert needs_normalization("こんにちは世界テスト") is True

    def test_needs_normalization_short(self):
        from applypilot.wizard.i18n import needs_normalization

        assert needs_normalization("hi") is False
