"""Migration 004: Add best_track_id column and tracks table."""

from __future__ import annotations

import sqlite3


def run(conn: sqlite3.Connection) -> None:
    # tracks table is auto-created by schema_from_dto via TrackDTO,
    # but ensure best_track_id column exists on jobs
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN best_track_id TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
