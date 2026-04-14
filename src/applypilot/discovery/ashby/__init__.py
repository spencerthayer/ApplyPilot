"""Ashby job board API scraper.

Ashby API: https://api.ashbyhq.com/posting-api/job-board/{slug}
Returns JSON with jobs array. Same pattern as Greenhouse.
"""

from __future__ import annotations

import logging
import re

import httpx

log = logging.getLogger(__name__)

ASHBY_API_BASE = "https://api.ashbyhq.com/posting-api/job-board"


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html).strip() if html else ""


def fetch_jobs(slug: str) -> list[dict] | None:
    """Fetch all jobs from an Ashby board. Returns list of job dicts or None."""
    url = f"{ASHBY_API_BASE}/{slug}"
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url)
            if resp.status_code in (404, 422):
                return None
            resp.raise_for_status()
            data = resp.json()
            return data.get("jobs", [])
    except Exception as e:
        log.warning("Ashby %s failed: %s", slug, e)
        return None


def parse_jobs(jobs: list[dict], company: str) -> list[dict]:
    """Parse Ashby API jobs into standard format."""
    results = []
    for job in jobs:
        title = job.get("title", "")
        job_url = job.get("jobUrl") or job.get("hostedUrl", "")
        location = job.get("location", "")
        department = job.get("department", "")
        description = _strip_html(job.get("descriptionHtml", ""))

        if not title or not job_url:
            continue

        results.append(
            {
                "url": job_url,
                "title": title,
                "company": company,
                "location": location,
                "department": department,
                "description": description[:500] if description else None,
                "full_description": description if len(description) > 200 else None,
                "site": "ashby",
                "strategy": "ashby_api",
            }
        )
    return results
