"""Shared HTTP/DB plumbing for direct-API ATS scrapers.

Greenhouse, Lever, and Ashby all expose the same shape of public job
board API: a slug-keyed endpoint that returns JSON listing every open
position for one employer. The orchestration around them was identical
across the three scrapers — fetch with retry, normalize, insert into
``jobs`` + emit a state transition, log per-employer + grand totals.

This module owns that orchestration so each scraper just provides:
  * the API URL template and yaml filename
  * a per-posting normalizer (title, location filter, etc.)
  * a ``strategy`` string for ``jobs.strategy``

See ``greenhouse.py``, ``lever.py``, ``ashby.py`` for the adapters.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Callable

import yaml

from applypilot.config import CONFIG_DIR
from applypilot.database import get_connection, init_db, write_with_retry

log = logging.getLogger(__name__)


_HEADERS = {
    "User-Agent": "ApplyPilot/1.0 (job-discovery)",
    "Accept": "application/json",
}


def _fetch_json(url: str, timeout: float = 20.0):
    """GET ``url`` and return parsed JSON. Plain ``urlopen`` — no auth needed
    for any of the three ATSes' public board endpoints."""
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_employers_yaml(filename: str) -> dict:
    """Read ``CONFIG_DIR/{filename}`` and return its ``employers`` block."""
    path = CONFIG_DIR / filename
    if not path.exists():
        log.warning("%s not found at %s", filename, path)
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("employers", {})


def fetch_with_retry(
    url: str,
    max_retries: int = 2,
    timeout: float = 20.0,
):
    """Fetch JSON with simple linear-backoff retry. Returns (payload, error).

    HTTP 404 short-circuits — that means the slug is wrong, no point
    waiting for the same answer twice. Any other error retries with a
    2 + 3*attempt second sleep between tries.
    """
    last_err: str | None = None
    for attempt in range(max_retries):
        try:
            return _fetch_json(url, timeout=timeout), None
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code} {e.reason}"
            if e.code == 404:
                return None, last_err
            time.sleep(2 + attempt * 3)
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(2 + attempt * 3)
    return None, last_err or "unknown error"


def insert_normalized_jobs(
    conn: sqlite3.Connection,
    jobs: list[dict],
    default_site: str,
    strategy: str,
) -> tuple[int, int]:
    """Insert normalized job dicts and emit state transitions.

    Each scraper builds a list of dicts with the same keys (url, title,
    description, full_description, location, application_url, posted_at,
    employer_name); this writes them. Returns (new, existing).
    """
    counts = {"new": 0, "existing": 0}
    now = datetime.now(timezone.utc).isoformat()

    def _do_inserts() -> None:
        counts["new"] = 0
        counts["existing"] = 0
        for job in jobs:
            url = job.get("url")
            if not url:
                continue
            full_description = job.get("full_description")
            detail_scraped_at = now if full_description else None
            site = job.get("employer_name", default_site)
            initial_state = "enriched" if full_description else "discovered"
            try:
                conn.execute(
                    "INSERT INTO jobs (url, title, salary, description, location, site, strategy, "
                    "discovered_at, posted_at, full_description, application_url, "
                    "detail_scraped_at, state) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (url, job.get("title"), None, job.get("description"),
                     job.get("location"), site, strategy, now,
                     job.get("posted_at"), full_description,
                     job.get("application_url"), detail_scraped_at, initial_state),
                )
                conn.execute(
                    "INSERT INTO job_state_transitions "
                    "(job_url, from_state, to_state, at, reason, metadata) "
                    "VALUES (?, NULL, ?, ?, ?, ?)",
                    (url, initial_state, now, f"discovered via {strategy}", None),
                )
                counts["new"] += 1
            except sqlite3.IntegrityError:
                counts["existing"] += 1

    write_with_retry(conn, _do_inserts)
    return counts["new"], counts["existing"]


# Type signature: (slug, employer_meta, accept_locs) → (jobs, error)
ScrapeOneFn = Callable[[str, dict, list[str]], tuple[list[dict], str | None]]


def run_ats_crawl(
    label: str,
    default_site: str,
    strategy: str,
    employers: dict,
    scrape_one: ScrapeOneFn,
) -> dict:
    """Drive a per-employer crawl using ``scrape_one`` for the per-tenant fetch.

    Args:
        label: human-readable name in log lines, e.g. "Greenhouse".
        default_site: ``jobs.site`` fallback when the per-job dict has no
            ``employer_name`` (shouldn't happen, but defensive).
        strategy: ``jobs.strategy`` value, e.g. "greenhouse_api".
        employers: ``{slug: meta_dict}``; ``meta_dict["name"]`` becomes the
            ``site`` column.
        scrape_one: function called per (slug, meta, accept_locs); returns
            (jobs_list, error_str_or_None). Each job dict must match the
            shape ``insert_normalized_jobs`` expects.

    Returns:
        ``{found, new, existing, employers, errors}``.
    """
    if not employers:
        log.warning("No %s employers configured.", label)
        return {"found": 0, "new": 0, "existing": 0, "employers": 0, "errors": []}

    # Lazy import to avoid a config-bootstrap cycle when the scraper modules
    # are imported at test-collection time.
    from applypilot import config as _cfg
    accept_locs = (_cfg.load_search_config()
                   .get("location", {}) or {}).get("accept_patterns", []) or []

    conn = get_connection()
    init_db()

    grand_new = 0
    grand_existing = 0
    grand_found = 0
    errors: list[str] = []

    log.info("%s crawl: %d employers", label, len(employers))
    for slug, emp in employers.items():
        name = emp.get("name", slug)
        try:
            jobs, err = scrape_one(slug, emp, accept_locs)
            if err:
                log.warning("  [%s] %s", slug, err)
                errors.append(f"{slug}: {err}")
                continue
            new, existing = insert_normalized_jobs(conn, jobs, default_site, strategy)
            grand_new += new
            grand_existing += existing
            grand_found += len(jobs)
            log.info("  [%s] %s: %d found (%d new, %d existing)",
                     slug, name, len(jobs), new, existing)
        except Exception as e:
            log.exception("%s scrape failed for %s: %s", label, slug, e)
            errors.append(f"{slug}: {e}")

    log.info("%s crawl done: %d found (%d new, %d existing) across %d employers",
             label, grand_found, grand_new, grand_existing, len(employers))

    return {
        "found": grand_found,
        "new": grand_new,
        "existing": grand_existing,
        "employers": len(employers),
        "errors": errors,
    }
