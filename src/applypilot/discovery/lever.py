"""Lever ATS direct API scraper.

Scrapes Lever-powered career sites (Highspot, Outreach, Rover, Plaid,
…) via the public postings endpoint at
``https://api.lever.co/v0/postings/{slug}?mode=json``.

Slugs in ``config/lever_employers.yaml``. The fetch + DB plumbing lives
in :mod:`applypilot.discovery.ats_common` — this module only owns the
Lever-specific URL template and per-posting normalizer.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from applypilot.discovery.ats_common import (
    fetch_with_retry,
    insert_normalized_jobs,
    load_employers_yaml,
    run_ats_crawl,
)
from applypilot.discovery.greenhouse import _location_ok, _strip_html

log = logging.getLogger(__name__)

LEVER_API = "https://api.lever.co/v0/postings/{slug}?mode=json"
_DEFAULT_SITE = "Lever"
_STRATEGY = "lever_api"


# ── Employer registry (kept for backwards compat with imports) ──────

def load_employers() -> dict:
    return load_employers_yaml("lever_employers.yaml")


# ── Lever-specific normalization ────────────────────────────────────

def _location_string(posting: dict) -> str:
    """Compose location from categories.location + workplaceType + allLocations.

    Treats ``on-site`` as the default-omit; remote/hybrid surface in the
    string so the downstream filter's remote-allowlist fires.
    """
    cats = posting.get("categories") or {}
    parts: list[str] = []
    wt = (posting.get("workplaceType") or "").lower()
    if wt and wt != "on-site":
        parts.append(wt)
    loc = cats.get("location")
    if loc:
        parts.append(str(loc))
    extra = cats.get("allLocations") or []
    if isinstance(extra, list):
        for e in extra:
            if e and e not in parts:
                parts.append(str(e))
    return ", ".join(parts)


def _description_text(posting: dict) -> str:
    """Plain-text body composed from description + lists + additional."""
    parts: list[str] = []
    if posting.get("description"):
        parts.append(_strip_html(posting["description"]))
    for section in posting.get("lists") or []:
        body = section.get("content") or ""
        if not body:
            continue
        title = section.get("text") or ""
        if title:
            parts.append(f"\n{title}")
        parts.append(_strip_html(body))
    if posting.get("additional"):
        parts.append(_strip_html(posting["additional"]))
    return "\n\n".join(p for p in parts if p)


def scrape_one_employer(
    slug: str,
    emp: dict,
    accept_locs: list[str],
    max_retries: int = 2,
) -> tuple[list[dict], str | None]:
    """Fetch all postings for one Lever board → list of normalized job dicts."""
    url = LEVER_API.format(slug=slug)
    payload, err = fetch_with_retry(url, max_retries=max_retries)
    if err:
        return [], err
    postings = payload if isinstance(payload, list) else []
    name = emp.get("name", slug)

    out: list[dict] = []
    for posting in postings:
        location = _location_string(posting)
        if not _location_ok(location, accept_locs):
            continue
        hosted_url = posting.get("hostedUrl")
        if not hosted_url:
            continue
        apply_url = posting.get("applyUrl") or hosted_url

        description = _description_text(posting)
        # Lever timestamps are ms-since-epoch; convert to ISO if present.
        created = posting.get("createdAt")
        posted_at: str | None = None
        if isinstance(created, (int, float)) and created > 0:
            posted_at = datetime.fromtimestamp(
                created / 1000, tz=timezone.utc,
            ).isoformat()

        out.append({
            "url": hosted_url,
            "title": posting.get("text") or "",
            "location": location or None,
            "description": (description[:500] if description else None),
            "full_description": description if len(description) > 200 else None,
            "application_url": apply_url,
            "employer_name": name,
            "employer_slug": slug,
            "posted_at": posted_at,
        })
    return out, None


# Kept exposed (some tests + pipeline.py still call this name)
def _insert_jobs(conn, jobs):
    return insert_normalized_jobs(conn, jobs, _DEFAULT_SITE, _STRATEGY)


# ── Public entry point ─────────────────────────────────────────────

def run_lever_discovery(employers: dict | None = None, workers: int = 1) -> dict:
    """Discover jobs from Lever-powered career sites."""
    if employers is None:
        employers = load_employers()
    return run_ats_crawl("Lever", _DEFAULT_SITE, _STRATEGY,
                         employers, scrape_one_employer)


# Kept for backwards compat (was imported by tests before the refactor).
def _fetch_json(url, timeout=20.0):  # pragma: no cover
    from applypilot.discovery.ats_common import _fetch_json as _f
    return _f(url, timeout)
