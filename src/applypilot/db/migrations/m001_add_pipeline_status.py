"""Migration 001: Add pipeline_status column and backfill."""

from __future__ import annotations

import sqlite3


def run(conn: sqlite3.Connection) -> None:
    from applypilot.db.sqlite.job_repo import SqliteJobRepository

    repo = SqliteJobRepository(conn)
    backfilled = repo.backfill_pipeline_status()
    if backfilled:
        import logging

        logging.getLogger(__name__).info("Backfilled pipeline_status for %d jobs", backfilled)
