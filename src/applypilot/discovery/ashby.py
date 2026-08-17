"""Ashby ATS direct-API scraper.

Scrapes Ashby-powered career sites (MotherDuck, Statsig, Deepgram,
PostHog, Vercel, OpenAI, Linear, Notion, Ramp, …) via the public
posting API at
``https://api.ashbyhq.com/posting-api/job-board/{slug}``.

Slugs in ``config/ashby_employers.yaml``. The fetch + DB plumbing lives
in :mod:`applypilot.discovery.ats_common` — this module only owns the
Ashby-specific URL template and per-posting normalizer.
"""
from __future__ import annotations

import logging

from applypilot.discovery.ats_common import (
    fetch_with_retry,
    insert_normalized_jobs,
    load_employers_yaml,
    run_ats_crawl,
)
from applypilot.discovery.greenhouse import _location_ok

log = logging.getLogger(__name__)

ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
_DEFAULT_SITE = "Ashby"
_STRATEGY = "ashby_api"


def load_employers() -> dict:
    return load_employers_yaml("ashby_employers.yaml")


def _location_string(posting: dict) -> str:
    """Compose location from primary + secondary + workplaceType + isRemote.

    Ashby splits location across several fields. We collapse to one string
    with ``remote``/``hybrid`` prepended (when applicable) so the
    downstream filter's remote-allowlist fires.
    """
    parts: list[str] = []
    wt = (posting.get("workplaceType") or "").lower()
    if wt == "remote" or posting.get("isRemote"):
        parts.append("remote")
    elif wt and wt != "on-site":
        parts.append(wt)
    primary = posting.get("location")
    if primary:
        parts.append(str(primary))
    for extra in posting.get("secondaryLocations") or []:
        if isinstance(extra, dict):
            extra = extra.get("location") or ""
        if extra and str(extra) not in parts:
            parts.append(str(extra))
    return ", ".join(parts)


def scrape_one_employer(
    slug: str,
    emp: dict,
    accept_locs: list[str],
    max_retries: int = 2,
) -> tuple[list[dict], str | None]:
    """Fetch all postings for one Ashby board → list of normalized job dicts."""
    url = ASHBY_API.format(slug=slug)
    payload, err = fetch_with_retry(url, max_retries=max_retries)
    if err:
        return [], err
    postings = (payload or {}).get("jobs") or []
    name = emp.get("name", slug)

    out: list[dict] = []
    for posting in postings:
        # Skip drafts / internal-only / scheduled-future postings.
        if posting.get("isListed") is False:
            continue
        location = _location_string(posting)
        if not _location_ok(location, accept_locs):
            continue
        job_url = posting.get("jobUrl")
        if not job_url:
            continue
        apply_url = posting.get("applyUrl") or job_url
        description = posting.get("descriptionPlain") or ""
        out.append({
            "url": job_url,
            "title": posting.get("title") or "",
            "location": location or None,
            "description": (description[:500] if description else None),
            "full_description": description if len(description) > 200 else None,
            "application_url": apply_url,
            "employer_name": name,
            "employer_slug": slug,
            "posted_at": posting.get("publishedAt") or None,
        })
    return out, None


def _insert_jobs(conn, jobs):
    return insert_normalized_jobs(conn, jobs, _DEFAULT_SITE, _STRATEGY)


def run_ashby_discovery(employers: dict | None = None, workers: int = 1) -> dict:
    """Discover jobs from Ashby-powered career sites."""
    if employers is None:
        employers = load_employers()
    return run_ats_crawl("Ashby", _DEFAULT_SITE, _STRATEGY,
                         employers, scrape_one_employer)


def _fetch_json(url, timeout=20.0):  # pragma: no cover
    from applypilot.discovery.ats_common import _fetch_json as _f
    return _f(url, timeout)
