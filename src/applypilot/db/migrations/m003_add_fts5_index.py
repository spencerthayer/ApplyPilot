"""Migration 003: Create FTS5 virtual table for full-text job search."""

from __future__ import annotations

import sqlite3


def run(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5(
            title, company, location, description, full_description,
            content='jobs', content_rowid='rowid'
        )
    """)
    conn.execute("""
                 INSERT
                 OR IGNORE INTO jobs_fts(rowid, title, company, location, description, full_description)
                 SELECT rowid,
                        COALESCE(title, ''),
                        COALESCE(company, ''),
                        COALESCE(location, ''),
                        COALESCE(description, ''),
                        COALESCE(full_description, '')
                 FROM jobs
                 """)
