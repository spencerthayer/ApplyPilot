"""Storage."""

__all__ = ["log", "GREENHOUSE_API_BASE", "_store_jobs"]

import logging
from datetime import datetime, timezone

from applypilot.db.dto import JobDTO

log = logging.getLogger(__name__)

# Greenhouse Job Board API endpoint
GREENHOUSE_API_BASE = "https://boards-api.greenhouse.io/v1/boards"


def _store_jobs(jobs: list[dict]) -> tuple[int, int]:
    """Store discovered jobs in the database. Returns (new, existing)."""
    from applypilot.bootstrap import get_app
    from applypilot.discovery.relevance_gate import is_relevant

    job_repo = get_app().container.job_repo
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    existing = 0
    skipped = 0

    for job in jobs:
        url = job.get("url", "")
        if not url:
            continue
        if job_repo.get_by_url(url):
            existing += 1
            continue
        if not is_relevant(job.get("title", ""), job.get("location", ""), job.get("description", "")):
            skipped += 1
            continue
        job_repo.upsert(
            JobDTO(
                url=url,
                title=job.get("title"),
                description=job.get("description", ""),
                location=job.get("location", ""),
                site=job.get("company"),
                strategy="greenhouse",
                discovered_at=now,
                full_description=job.get("description"),
                application_url=url,
                detail_scraped_at=now,
            )
        )
        new += 1
        try:
            from applypilot.analytics.helpers import emit_job_discovered

            emit_job_discovered(url, job.get("company", ""), job.get("title", ""))
        except Exception:
            pass

    if skipped:
        log.info("[greenhouse] Skipped %d irrelevant jobs (relevance gate)", skipped)
    return new, existing
