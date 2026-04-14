"""Storage."""

import logging
from datetime import datetime, timezone

from applypilot.bootstrap import get_app
from applypilot.db.dto import JobDTO

log = logging.getLogger(__name__)
_QUARANTINE_HTTP_STATUSES = {401, 404, 422}


def store_results(jobs: list[dict], employers: dict) -> tuple[int, int]:
    """Store corporate jobs in DB. Returns (new, existing)."""
    job_repo = get_app().container.job_repo
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    existing = 0

    for job in jobs:
        url = job.get("apply_url", "")
        if not url:
            emp = employers.get(job.get("employer_key", ""), {})
            if emp and job.get("external_path"):
                url = f"{emp['base_url']}/{emp['site_id']}{job['external_path']}"
        if not url:
            continue

        if job_repo.get_by_url(url):
            existing += 1
            continue

        description = job.get("full_description", "")
        short_desc = description[:500] if description else None
        full_description = description if len(description) > 200 else None
        detail_scraped_at = now if full_description else None

        job_repo.upsert(
            JobDTO(
                url=url,
                title=job.get("title"),
                description=short_desc,
                location=job.get("location"),
                site=job.get("employer_name", "Corporate"),
                strategy="workday_api",
                discovered_at=now,
                full_description=full_description,
                application_url=url,
                detail_scraped_at=detail_scraped_at,
                detail_error=job.get("detail_error"),
            )
        )
        new += 1
        try:
            from applypilot.analytics.helpers import emit_job_discovered

            emit_job_discovered(url, job.get("employer_name", ""), job.get("title", ""))
        except Exception:
            pass

    return new, existing
