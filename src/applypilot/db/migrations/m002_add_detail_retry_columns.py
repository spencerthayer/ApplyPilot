"""Migration 002: Ensure detail retry columns exist (idempotent)."""

from __future__ import annotations

import sqlite3


def run(conn: sqlite3.Connection) -> None:
    pass  # Handled by migrate_from_dto — marker only
