"""Write-with-retry for SQLite lock contention.

Extracted from database.py — handles concurrent streaming stages
and apply process all writing simultaneously.
"""

from __future__ import annotations

import logging
import sqlite3
import time

log = logging.getLogger(__name__)


def write_with_retry(
        conn: sqlite3.Connection,
        fn,
        *args,
        max_retries: int = 8,
        base_delay: float = 0.25,
        **kwargs,
) -> None:
    """Execute a write function plus commit with retry on lock."""
    for attempt in range(max_retries):
        try:
            fn(*args, **kwargs)
            conn.commit()
            return
        except sqlite3.OperationalError as e:
            if "database is locked" not in str(e):
                raise
            try:
                conn.rollback()
            except Exception:
                pass
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                log.warning(
                    "DB locked on write (attempt %d/%d), retry in %.2fs",
                    attempt + 1,
                    max_retries,
                    delay,
                )
                time.sleep(delay)
            else:
                log.error("DB write locked: giving up after %d attempts", max_retries)
                raise


def commit_with_retry(
        conn: sqlite3.Connection,
        max_retries: int = 8,
        base_delay: float = 0.25,
) -> None:
    """Commit with exponential backoff on lock."""
    for attempt in range(max_retries):
        try:
            conn.commit()
            return
        except sqlite3.OperationalError as e:
            if "database is locked" not in str(e):
                raise
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                log.warning(
                    "DB locked on commit (attempt %d/%d), retry in %.2fs",
                    attempt + 1,
                    max_retries,
                    delay,
                )
                time.sleep(delay)
            else:
                raise
