"""Lever job board API scraper.

Lever API: https://api.lever.co/v0/postings/{slug}
Returns JSON array of job postings. Same pattern as Greenhouse.
"""

from __future__ import annotations

import logging
import re

import httpx

log = logging.getLogger(__name__)

LEVER_API_BASE = "https://api.lever.co/v0/postings"


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html).strip() if html else ""


def fetch_jobs(slug: str) -> list[dict] | None:
    """Fetch all jobs from a Lever board. Returns list of job dicts or None."""
    url = f"{LEVER_API_BASE}/{slug}"
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, params={"mode": "json"})
            if resp.status_code in (404, 422):
                return None
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        log.warning("Lever %s failed: %s", slug, e)
        return None


def parse_jobs(jobs: list[dict], company: str) -> list[dict]:
    """Parse Lever API jobs into standard format."""
    results = []
    for job in jobs:
        title = job.get("text", "")
        job_url = job.get("hostedUrl") or job.get("applyUrl", "")
        location = job.get("categories", {}).get("location", "")
        department = job.get("categories", {}).get("department", "")
        description = _strip_html(job.get("descriptionPlain", "") or job.get("description", ""))

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
                "site": "lever",
                "strategy": "lever_api",
            }
        )
    return results
