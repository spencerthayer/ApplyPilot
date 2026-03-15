"""Detect ghosted applications — applied jobs with no email response.

A job is marked "ghosted" if:
  - applied_at is more than N days ago (default 7)
  - No tracking emails exist for it
  - Current tracking_status is None (no prior classification)
"""

import logging
import sqlite3
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)


def detect_ghosted(
    applied_jobs: list[dict],
    ghosted_days: int = 7,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Mark jobs as ghosted if they have no response after N days.

    Args:
        applied_jobs: List of applied job dicts from get_applied_jobs().
        ghosted_days: Days after application before marking as ghosted.
        conn: Database connection.

    Returns:
        Number of jobs marked as ghosted.
    """
    from applypilot.database import get_connection, update_tracking_status

    if conn is None:
        conn = get_connection()

    cutoff = datetime.now(timezone.utc) - timedelta(days=ghosted_days)
    ghosted_count = 0

    for job in applied_jobs:
        # Skip if already has a tracking status
        if job.get("tracking_status"):
            continue

        # Check if applied more than N days ago
        applied_at = job.get("applied_at")
        if not applied_at:
            continue

        try:
            applied_dt = datetime.fromisoformat(applied_at.replace("Z", "+00:00"))
            if applied_dt.tzinfo is None:
                applied_dt = applied_dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue

        if applied_dt > cutoff:
            continue  # Too recent

        # Check if any tracking emails exist for this job
        email_count = conn.execute(
            "SELECT COUNT(*) FROM tracking_emails WHERE job_url = ?",
            (job["url"],),
        ).fetchone()[0]

        if email_count > 0:
            continue  # Has emails, not ghosted

        # Mark as ghosted
        if update_tracking_status(job["url"], "ghosted", conn):
            ghosted_count += 1
            log.info("Marked as ghosted: %s (%s)", job.get("title", ""), job["url"][:60])

    return ghosted_count
