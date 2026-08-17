"""BuiltIn (builtin.com) HTML scraper.

builtin.com is fully server-side rendered (Drupal stack) — no Algolia, no
JSON API. We just paginate ``/jobs/{category}?city={city}&page=N`` and
parse the rendered job cards. Each card carries job_id, slug, title,
company slug, work mode, location, salary range, seniority, and a short
summary, so the data is rich enough to skip enrichment and seed scoring
directly.

Configuration lives in ``searches.yaml`` under ``builtin:``::

    builtin:
      cities: ["Seattle"]          # empty list → global (no city filter)
      categories: ["dev-engineering"]
      remote_only: false           # if true, prepend /remote to the path

Defaults: categories=["dev-engineering"], cities=[] (global), remote_only=False.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

from applypilot import config
from applypilot.database import get_connection, init_db, write_with_retry

log = logging.getLogger(__name__)


_BASE = "https://builtin.com"
_HEADERS = {
    # Real-Chrome UA — builtin.com responds with empty body to obvious bot UAs.
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "br", "li", "div"):
            self.parts.append("\n")

    def handle_data(self, data):
        if data.strip():
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        return raw.strip()


def _strip_html(s: str | None) -> str:
    if not s:
        return ""
    p = _HTMLStripper()
    try:
        p.feed(s)
    except Exception:
        return s
    return p.text()


def _build_url(category: str, city: str | None, remote_only: bool, page: int) -> str:
    """Construct a paginated category page URL."""
    path_parts = ["jobs"]
    if remote_only:
        path_parts.append("remote")
    path_parts.append(category)
    path = "/".join(path_parts)

    params: list[tuple[str, str]] = []
    if city:
        params.append(("city", city))
    if page > 1:
        params.append(("page", str(page)))

    qs = "?" + urllib.parse.urlencode(params) if params else ""
    return f"{_BASE}/{path}{qs}"


def _fetch(url: str, timeout: float = 20.0) -> str | None:
    """GET the page; returns HTML or None on failure."""
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ""  # past last page
        log.warning("builtin.com fetch %s: HTTP %d", url, e.code)
        return None
    except Exception as e:
        log.warning("builtin.com fetch %s: %s", url, e)
        return None


# ── Card parsing ──────────────────────────────────────────────────────


# Each job card sits inside <div id="job-card-{id}"> ... </div>. The next
# job card OR the closing <div id="job-cards-end"> marker terminates it.
_CARD_RE = re.compile(
    r'(<div id="job-card-(?P<id>\d+)".*?)'
    r'(?=<div id="job-card-\d+"|<div id="job-cards-end")',
    re.DOTALL,
)
_TITLE_RE = re.compile(
    r'<a href="(?P<url>/job/[^"]+)"[^>]*>\s*'
    r'(?:<[^>]+>\s*)*(?P<title>[^<]{3,200})',
)
_COMPANY_RE = re.compile(r'href="/company/(?P<slug>[a-z0-9][a-z0-9-]+)"')
_BARLOW_RE = re.compile(r'<span class="font-barlow[^"]*">([^<]+)</span>')
_SUMMARY_RE = re.compile(
    r'class="fs-sm fw-regular mb-md text-gray-04">([^<]+)<'
)


def _slug_to_company_name(slug: str) -> str:
    """Best-effort: 'possible-finance' → 'Possible Finance'."""
    return " ".join(part.capitalize() for part in slug.split("-"))


def _parse_card(card_html: str, job_id: str) -> dict | None:
    """Extract one job card's structured fields. Returns None if essentials missing."""
    title_m = _TITLE_RE.search(card_html)
    if not title_m:
        return None
    relative_url = title_m.group("url")
    title = title_m.group("title").strip()

    company_slug = None
    company_m = _COMPANY_RE.search(card_html)
    if company_m:
        company_slug = company_m.group("slug")

    summary = ""
    summary_m = _SUMMARY_RE.search(card_html)
    if summary_m:
        summary = summary_m.group(1).strip()

    # font-barlow spans hold work mode / location / salary / seniority in
    # that order, repeated twice (mobile + desktop layouts). Dedup
    # in-place while keeping order.
    barlow = _BARLOW_RE.findall(card_html)
    seen: set[str] = set()
    fields: list[str] = []
    for b in barlow:
        b = b.strip()
        if b and b not in seen:
            seen.add(b)
            fields.append(b)

    work_mode = None  # "Remote", "Hybrid", "In-Office", "Remote or Hybrid", ...
    location = None   # "Seattle, WA, USA"
    salary = None     # "180K-220K Annually"
    seniority = None  # "Senior level"
    for f in fields:
        if any(k in f for k in ("Remote", "Hybrid", "In-Office", "On-site")):
            if not work_mode:
                work_mode = f
        elif re.search(r"\d", f) and ("Annually" in f or "Hourly" in f or "K-" in f or "$" in f):
            if not salary:
                salary = f
        elif "level" in f.lower() or "intern" in f.lower() or "expert" in f.lower():
            if not seniority:
                seniority = f
        elif re.search(r"[A-Z][a-z]+,\s*[A-Z]{2}", f) or "USA" in f or "USC" in f:
            if not location:
                location = f

    full_url = urllib.parse.urljoin(_BASE, relative_url)
    company_name = _slug_to_company_name(company_slug) if company_slug else "BuiltIn"

    job: dict = {
        "url": full_url,
        "external_id": job_id,
        "title": title,
        "employer_name": company_name,
        "company_slug": company_slug,
        "location": location,
        "work_mode": work_mode,
        "salary": salary,
        "seniority": seniority,
        "description": _strip_html(summary) or None,
    }
    return job


def _parse_listing_page(html: str) -> list[dict]:
    """Parse a category-listing page into job dicts."""
    jobs: list[dict] = []
    seen_ids: set[str] = set()
    for m in _CARD_RE.finditer(html):
        job_id = m.group("id")
        if job_id in seen_ids:
            continue
        seen_ids.add(job_id)
        job = _parse_card(m.group(1), job_id)
        if job:
            jobs.append(job)
    return jobs


# ── DB insert ─────────────────────────────────────────────────────────


def _insert_jobs(conn: sqlite3.Connection, jobs: list[dict]) -> tuple[int, int]:
    counts = {"new": 0, "existing": 0}
    now = datetime.now(timezone.utc).isoformat()

    def _do_inserts() -> None:
        counts["new"] = 0
        counts["existing"] = 0
        for job in jobs:
            url = job.get("url")
            if not url:
                continue
            site = job.get("employer_name") or "BuiltIn"
            description = job.get("description")
            initial_state = "discovered"
            try:
                conn.execute(
                    "INSERT INTO jobs (url, title, salary, description, location, "
                    "site, strategy, discovered_at, application_url, state) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        url, job.get("title"), job.get("salary"),
                        description, job.get("location"),
                        site, "builtin_html", now, url, initial_state,
                    ),
                )
                conn.execute(
                    "INSERT INTO job_state_transitions "
                    "(job_url, from_state, to_state, at, reason, metadata) "
                    "VALUES (?, NULL, ?, ?, ?, ?)",
                    (url, initial_state, now, "discovered via builtin_html", None),
                )
                counts["new"] += 1
            except sqlite3.IntegrityError:
                counts["existing"] += 1

    write_with_retry(conn, _do_inserts)
    return counts["new"], counts["existing"]


# ── Public entry point ────────────────────────────────────────────────


# Hard cap on pages per (city, category) — observed Seattle/dev-engineering
# tops out around page 22; 50 is a comfortable ceiling that triggers when
# something goes wrong upstream rather than after eating an hour.
_MAX_PAGES = 50
# builtin.com's Cloudflare in front of the static cache rate-limits at
# roughly 60 req/min; 0.5s between page fetches keeps us comfortably under.
_PAGE_DELAY = 0.5


def run_builtin_discovery(workers: int = 1) -> dict:
    """Discover jobs on builtin.com per searches.yaml builtin config.

    Args:
        workers: ignored — builtin paginates one URL at a time, no parallelism win.
    """
    search_cfg = config.load_search_config()
    builtin_cfg = search_cfg.get("builtin", {}) or {}

    cities: list[str] = builtin_cfg.get("cities", []) or []
    categories: list[str] = builtin_cfg.get("categories", []) or ["dev-engineering"]
    remote_only: bool = bool(builtin_cfg.get("remote_only", False))

    # cities=[] means "global" — issue one crawl per category with no city filter.
    targets: list[tuple[str | None, str]] = []
    if cities:
        for city in cities:
            for cat in categories:
                targets.append((city, cat))
    else:
        for cat in categories:
            targets.append((None, cat))

    log.info(
        "BuiltIn discovery: %d targets (cities=%s, categories=%s, remote_only=%s)",
        len(targets), cities or ["<global>"], categories, remote_only,
    )

    conn = get_connection()
    init_db()

    grand_new = 0
    grand_existing = 0
    grand_pages = 0
    grand_jobs = 0

    for city, category in targets:
        label = f"{category}{f' / {city}' if city else ''}"
        log.info("  [%s] paginating...", label)

        target_new = 0
        target_existing = 0
        for page in range(1, _MAX_PAGES + 1):
            url = _build_url(category, city, remote_only, page)
            html = _fetch(url)
            if html is None:
                # Network/HTTP-error — bail this target, move to next.
                log.warning("  [%s] page %d fetch failed; stopping target", label, page)
                break
            if not html.strip():
                # 404 or empty body marks past last page.
                break

            jobs = _parse_listing_page(html)
            grand_pages += 1
            if not jobs:
                # Page rendered but no job cards — past last page.
                break

            new, existing = _insert_jobs(conn, jobs)
            target_new += new
            target_existing += existing
            grand_jobs += len(jobs)
            log.info(
                "  [%s] page %d: %d found (%d new, %d existing)",
                label, page, len(jobs), new, existing,
            )
            time.sleep(_PAGE_DELAY)

        grand_new += target_new
        grand_existing += target_existing
        log.info(
            "  [%s] done: %d new, %d existing",
            label, target_new, target_existing,
        )

    log.info(
        "BuiltIn discovery done: %d targets, %d pages, %d jobs (%d new, %d existing)",
        len(targets), grand_pages, grand_jobs, grand_new, grand_existing,
    )
    return {
        "targets": len(targets),
        "pages": grand_pages,
        "jobs": grand_jobs,
        "new": grand_new,
        "existing": grand_existing,
    }
