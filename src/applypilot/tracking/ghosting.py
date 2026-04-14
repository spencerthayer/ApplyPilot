"""Detect ghosted applications — applied jobs with no email response.

A job is marked "ghosted" if:
  - applied_at is more than N days ago (default 7)
  - No tracking emails exist for it
  - Current tracking_status is None (no prior classification)
"""

import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)


def detect_ghosted(
    applied_jobs: list[dict],
    ghosted_days: int = 7,
        conn=None,
) -> int:
    """Mark jobs as ghosted if they have no response after N days.

    Args:
        applied_jobs: List of applied job dicts from get_applied_jobs().
        ghosted_days: Days after application before marking as ghosted.
        conn: Ignored — kept for backward compat. All DB via repos.

    Returns:
        Number of jobs marked as ghosted.
    """
    from applypilot.tracking._compat import (
        update_tracking_status,
    )

    def _tracking_repo():
        from applypilot.bootstrap import get_app

        return get_app().container.tracking_repo

    cutoff = datetime.now(timezone.utc) - timedelta(days=ghosted_days)
    ghosted_count = 0

    for job in applied_jobs:
        if job.get("tracking_status"):
            continue

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
            continue

        # Check if any tracking emails exist via repo
        emails = _tracking_repo().get_emails(job["url"])
        if emails:
            continue

        if update_tracking_status(job["url"], "ghosted"):
            ghosted_count += 1
            log.info("Marked as ghosted: %s (%s)", job.get("title", ""), job["url"][:60])

    return ghosted_count
