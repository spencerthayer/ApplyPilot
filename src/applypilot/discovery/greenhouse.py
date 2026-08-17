"""Greenhouse ATS direct API scraper.

Scrapes Greenhouse-powered career sites (Temporal, Pulumi, Anduril, Stripe,
Databricks, etc.) via the public board API. Zero LLM, zero browser — pure HTTP.

Board API: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true

Company slugs are configured in config/greenhouse_employers.yaml.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import urllib.error
import urllib.request
from html.parser import HTMLParser

import yaml

from applypilot import config
from applypilot.config import CONFIG_DIR

log = logging.getLogger(__name__)


GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
_HEADERS = {
    "User-Agent": "ApplyPilot/1.0 (job-discovery)",
    "Accept": "application/json",
}


# ── Employer registry ─────────────────────────────────────────────────

def load_employers() -> dict:
    """Load Greenhouse employer registry from config/greenhouse_employers.yaml."""
    path = CONFIG_DIR / "greenhouse_employers.yaml"
    if not path.exists():
        log.warning("greenhouse_employers.yaml not found at %s", path)
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("employers", {})


# ── HTML strip helper ─────────────────────────────────────────────────

class _HTMLStripper(HTMLParser):
    """Strip HTML tags, preserve text content and line breaks."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style"):
            self._skip = True
        elif tag in ("p", "br", "li", "div", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        # Collapse whitespace
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _strip_html(html: str) -> str:
    if not html:
        return ""
    s = _HTMLStripper()
    try:
        s.feed(html)
    except Exception:
        return html  # fallback: return raw
    return s.text()


# ── Location filter ───────────────────────────────────────────────────

def _load_location_filter(search_cfg: dict | None = None):
    if search_cfg is None:
        search_cfg = config.load_search_config()
    loc = search_cfg.get("location", {}) or {}
    accept = loc.get("accept_patterns", []) or []
    return accept


def _location_ok(location: str | None, accept: list[str]) -> bool:
    """Return True if location passes the user's filter.

    Remote is always accepted. Otherwise location must contain one of the
    accept patterns (case-insensitive).
    """
    if not location:
        # Empty location — let it through (some Greenhouse jobs omit location).
        return True
    loc = location.lower()
    if any(r in loc for r in ("remote", "anywhere", "work from home", "wfh")):
        return True
    if not accept:
        return True
    return any(a.lower() in loc for a in accept)


# ── HTTP fetch ────────────────────────────────────────────────────────

def _fetch_json(url: str, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── Per-employer scrape ───────────────────────────────────────────────

def scrape_one_employer(
    slug: str,
    emp: dict,
    accept_locs: list[str],
    max_retries: int = 2,
) -> tuple[list[dict], str | None]:
    """Fetch all jobs from one Greenhouse board.

    Returns (jobs, error) — jobs is a list of normalized dicts, error is
    non-None if the fetch failed.
    """
    from applypilot.discovery.ats_common import fetch_with_retry
    url = GREENHOUSE_API.format(slug=slug) + "?content=true"
    data, err = fetch_with_retry(url, max_retries=max_retries)
    if err:
        return [], err
    jobs_raw = (data or {}).get("jobs", [])
    name = emp.get("name", slug)

    out = []
    for job in jobs_raw:
        location_name = (job.get("location") or {}).get("name") or ""
        if not _location_ok(location_name, accept_locs):
            continue

        abs_url = job.get("absolute_url")
        if not abs_url:
            continue

        content_html = job.get("content") or ""
        # Greenhouse sometimes returns HTML-entity-encoded content
        content_html = content_html.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        description = _strip_html(content_html)

        # Greenhouse returns `updated_at` + `first_published`. Prefer the
        # earlier posting date when available.
        posted_at = job.get("first_published") or job.get("updated_at") or None

        out.append({
            "url": abs_url,
            "title": job.get("title") or "",
            "location": location_name or None,
            "description": description[:500] if description else None,
            "full_description": description if len(description) > 200 else None,
            "application_url": abs_url,
            "employer_name": name,
            "employer_slug": slug,
            "posted_at": posted_at,
        })

    return out, None


# ── DB insert / driver ────────────────────────────────────────────────
# Both sit in ats_common now; thin shims kept here for any external
# imports (existing tests reference greenhouse._insert_jobs by name).

def _insert_jobs(conn: sqlite3.Connection, jobs: list[dict]) -> tuple[int, int]:
    from applypilot.discovery.ats_common import insert_normalized_jobs
    return insert_normalized_jobs(conn, jobs, "Greenhouse", "greenhouse_api")


def run_greenhouse_discovery(employers: dict | None = None, workers: int = 1) -> dict:
    """Discover jobs from Greenhouse-powered career sites."""
    from applypilot.discovery.ats_common import run_ats_crawl
    if employers is None:
        employers = load_employers()
    return run_ats_crawl("Greenhouse", "Greenhouse", "greenhouse_api",
                         employers, scrape_one_employer)
