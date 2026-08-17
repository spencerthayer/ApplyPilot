"""Workday ATS direct API scraper: searches employer career portals.

Scrapes Workday-powered career sites (TD, RBC, NVIDIA, Salesforce, etc.)
via the undocumented CXS JSON API. Zero LLM, zero browser -- pure HTTP.

Employer registry is loaded from config/employers.yaml instead of being
hardcoded. Supports sequential search + detail fetching with proxy.
"""

import json
import logging
import re
import sqlite3
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser

import yaml

from applypilot import config
from applypilot.config import CONFIG_DIR
from applypilot.database import get_connection, init_db, write_with_retry

log = logging.getLogger(__name__)
_QUARANTINE_HTTP_STATUSES = {401, 404, 422}


def _exception_summary(exc: Exception) -> str:
    """Return a minimal exception summary safe for logs."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"{exc.__class__.__name__}(status={exc.code})"
    return exc.__class__.__name__


class WorkdayEmployerFailure(RuntimeError):
    """A Workday employer failed in a way that should count as an error."""

    def __init__(self, message: str, *, quarantine: bool = False):
        super().__init__(message)
        self.quarantine = quarantine


# -- Employer registry from YAML --------------------------------------------

def load_employers() -> dict:
    """Load Workday employer registry from config/employers.yaml."""
    path = CONFIG_DIR / "employers.yaml"
    if not path.exists():
        log.warning("employers.yaml not found at %s", path)
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("employers", {})


# -- Location filtering from search config -----------------------------------

def _load_location_filter(search_cfg: dict | None = None):
    """Load location accept/reject lists from search config."""
    if search_cfg is None:
        search_cfg = config.load_search_config()

    accept = search_cfg.get("location_accept", [])
    reject = search_cfg.get("location_reject_non_remote", [])
    return accept, reject


def _location_ok(location: str | None, accept: list[str], reject: list[str]) -> bool:
    """Check if a job location passes the user's location filter."""
    if not location:
        return True

    loc = location.lower()

    if any(r in loc for r in ("remote", "anywhere", "work from home", "wfh", "distributed")):
        return True

    for r in reject:
        if r.lower() in loc:
            return False

    for a in accept:
        if a.lower() in loc:
            return True

    return False


# -- HTML stripper -----------------------------------------------------------

class _HTMLStripper(HTMLParser):
    """Strip HTML tags, keep text content."""

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False
        elif tag in ("p", "div", "li", "tr"):
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"[^\S\n]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def strip_html(html: str) -> str:
    """Convert HTML to plain text."""
    if not html:
        return ""
    stripper = _HTMLStripper()
    stripper.feed(html)
    return stripper.get_text()


# -- Proxy -------------------------------------------------------------------

_opener = None


def setup_proxy(proxy_str: str | None) -> None:
    """Configure a global urllib opener with proxy support."""
    global _opener
    if not proxy_str:
        _opener = urllib.request.build_opener()
        return

    parts = proxy_str.split(":")
    if len(parts) == 4:
        host, port, user, passwd = parts
        proxy_url = f"http://{user}:{passwd}@{host}:{port}"
    elif len(parts) == 2:
        proxy_url = f"http://{parts[0]}:{parts[1]}"
    else:
        log.warning("Proxy format not recognized; expected host:port or host:port:user:pass")
        _opener = urllib.request.build_opener()
        return

    proxy_handler = urllib.request.ProxyHandler({
        "http": proxy_url,
        "https": proxy_url,
    })
    _opener = urllib.request.build_opener(proxy_handler)
    log.info("Proxy configured")


def _urlopen(req, timeout=30):
    """Open a URL using the configured opener (with or without proxy)."""
    if _opener:
        return _opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


# -- Workday API -------------------------------------------------------------

_COMMON_SITE_ID_CANDIDATES = (
    "External",
    "careers",
    "Careers",
    "jobs",
    "Jobs",
    "CorporateCareers",
    "SearchJobs",
    "External_Career_Site",
)


def _candidate_site_ids(employer: dict) -> list[str]:
    site_id = (employer.get("site_id") or "").strip()
    name = (employer.get("name") or "").strip()
    normalized_name = re.sub(r"[^A-Za-z0-9]+", "", name)
    lower_name = normalized_name.lower()
    base_candidates = [site_id, normalized_name, lower_name, *_COMMON_SITE_ID_CANDIDATES]
    seen: set[str] = set()
    candidates: list[str] = []
    for candidate in base_candidates:
        if not candidate:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        candidates.append(candidate)
    return candidates


def _site_path_is_live(base_url: str, site_id: str) -> bool:
    for path in (f"/{site_id}", f"/en-US/{site_id}"):
        req = urllib.request.Request(
            f"{base_url}{path}",
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        )
        try:
            with _urlopen(req, timeout=8):
                return True
        except urllib.error.HTTPError:
            continue
        except Exception:
            continue
    return False


def _workday_search_request(employer: dict, search_text: str, limit: int, offset: int, site_id: str) -> dict:
    url = f"{employer['base_url']}/wday/cxs/{employer['tenant']}/{site_id}/jobs"
    payload = json.dumps({
        "appliedFacets": {},
        "limit": limit,
        "offset": offset,
        "searchText": search_text,
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    with _urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _try_discover_site_id(employer: dict, search_text: str, limit: int) -> str | None:
    candidates = _candidate_site_ids(employer)
    for candidate in candidates:
        if candidate == employer.get("site_id"):
            continue
        if not _site_path_is_live(employer["base_url"], candidate):
            continue
        try:
            _workday_search_request(employer, search_text, limit, 0, candidate)
            return candidate
        except urllib.error.HTTPError:
            continue
        except Exception:
            continue
    return None

def workday_search(employer: dict, search_text: str, limit: int = 20, offset: int = 0) -> dict:
    """Search jobs via Workday CXS API. Returns JSON with total + jobPostings."""
    try:
        return _workday_search_request(employer, search_text, limit, offset, employer["site_id"])
    except urllib.error.HTTPError as e:
        if offset == 0 and e.code in _QUARANTINE_HTTP_STATUSES:
            discovered_site_id = _try_discover_site_id(employer, search_text, limit)
            if discovered_site_id and discovered_site_id != employer.get("site_id"):
                employer["site_id"] = discovered_site_id
                return _workday_search_request(employer, search_text, limit, offset, employer["site_id"])
        raise


def workday_detail(employer: dict, external_path: str) -> dict:
    """Fetch full job detail via Workday CXS API."""
    url = f"{employer['base_url']}/wday/cxs/{employer['tenant']}/{employer['site_id']}{external_path}"

    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

    with _urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


# -- Search + paginate -------------------------------------------------------

def search_employer(
    employer_key: str,
    employer: dict,
    search_text: str,
    location_filter: bool = True,
    max_results: int = 0,
    accept_locs: list[str] | None = None,
    reject_locs: list[str] | None = None,
) -> list[dict]:
    """Search an employer, paginate through all results, optionally filter by location."""
    log.info("%s: starting Workday search", employer["name"])

    all_jobs: list[dict] = []
    offset = 0
    page_size = 20
    max_pages = 25  # Cap at 500 results
    total = None

    while True:
        data = None
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                data = workday_search(employer, search_text, limit=page_size, offset=offset)
                break
            except urllib.error.HTTPError as e:
                last_err = e
                # 4xx is permanent (dead tenant / wrong site_id — e.g. the
                # Netflix 422): retrying just hammers a dead endpoint.
                if e.code < 500:
                    break
                # 5xx is usually transient (Remitly threw a one-off
                # Cloudflare 520 mid-run 2026-06-10, healthy seconds later
                # — it cost the whole employer for that run).
                if attempt < 2:
                    wait = 5 * (2 ** attempt)
                    log.warning("%s: HTTP %d at offset %d — retry %d/2 in %ds",
                                employer["name"], e.code, offset, attempt + 1, wait)
                    time.sleep(wait)
            except Exception as e:
                # URLError / timeout / connection reset — same treatment
                last_err = e
                if attempt < 2:
                    wait = 5 * (2 ** attempt)
                    log.warning("%s: %s at offset %d — retry %d/2 in %ds",
                                employer["name"], e, offset, attempt + 1, wait)
                    time.sleep(wait)
        if data is None:
            log.error("%s: API error at offset %d: %s", employer["name"], offset, last_err)
            break

        if total is None:
            total = data.get("total", 0)
            log.info("%s: first Workday page received", employer["name"])

        postings = data.get("jobPostings", [])
        if not postings:
            break

        for j in postings:
            loc = j.get("locationsText", "")
            if location_filter and accept_locs is not None and reject_locs is not None:
                if not _location_ok(loc, accept_locs, reject_locs):
                    continue

            all_jobs.append({
                "title": j.get("title", ""),
                "location": loc,
                "posted": j.get("postedOn", ""),
                "external_path": j.get("externalPath", ""),
                "employer_key": employer_key,
                "employer_name": employer["name"],
            })

        offset += page_size
        page_num = offset // page_size
        if offset >= total:
            break
        if page_num >= max_pages:
            log.info("%s: Workday page cap reached", employer["name"])
            break
        if max_results and len(all_jobs) >= max_results:
            all_jobs = all_jobs[:max_results]
            break

    log.info("%s: Workday search complete", employer["name"])
    return all_jobs


# -- Fetch details -----------------------------------------------------------

def _fetch_one_detail(employer: dict, job: dict) -> dict:
    """Fetch detail for a single job."""
    try:
        detail = workday_detail(employer, job["external_path"])
        info = detail.get("jobPostingInfo", {})

        raw_desc = info.get("jobDescription", "")
        job["full_description"] = strip_html(raw_desc)
        job["apply_url"] = info.get("externalUrl", "")
        job["job_req_id"] = info.get("jobReqId", "")
        job["time_type"] = info.get("timeType", "")
        job["remote_type"] = info.get("remoteType", "")
        # `startDate` is the ISO posting go-live date (e.g. "2026-05-12").
        # The search-results `postedOn` is only a relative string
        # ("Posted 3 Days Ago"), so we don't use it.
        job["posted_at"] = info.get("startDate") or None

    except Exception as e:
        job["full_description"] = ""
        job["apply_url"] = ""
        job["detail_error"] = str(e)

    return job


def fetch_details(employer: dict, jobs: list[dict]) -> list[dict]:
    """Fetch full description + apply URL for each job sequentially."""
    log.info("%s: fetching Workday details", employer["name"])

    completed = 0
    errors = 0

    for job in jobs:
        _fetch_one_detail(employer, job)
        completed += 1
        if "detail_error" in job:
            errors += 1

        if completed % 20 == 0 or completed == len(jobs):
            log.debug("%s: detail fetch checkpoint", employer["name"])

    log.info("%s: Workday detail fetch complete", employer["name"])
    return jobs


# -- DB storage --------------------------------------------------------------

def store_results(conn: sqlite3.Connection, jobs: list[dict], employers: dict) -> tuple[int, int]:
    """Store corporate jobs in DB. Returns (new, existing)."""
    now = datetime.now(timezone.utc).isoformat()
    counts = {"new": 0, "existing": 0}

    def _do_inserts() -> None:
        counts["new"] = 0
        counts["existing"] = 0
        for job in jobs:
            url = job.get("apply_url", "")
            if not url:
                emp = employers.get(job.get("employer_key", ""), {})
                if emp and job.get("external_path"):
                    url = f"{emp['base_url']}/{emp['site_id']}{job['external_path']}"
            if not url:
                continue

            description = job.get("full_description", "")
            short_desc = description[:500] if description else None
            full_description = description if len(description) > 200 else None
            detail_scraped_at = now if full_description else None
            detail_error = job.get("detail_error")

            site = job.get("employer_name", "Corporate")
            strategy = "workday_api"

            try:
                conn.execute(
                    "INSERT INTO jobs (url, title, salary, description, location, site, strategy, "
                    "discovered_at, posted_at, full_description, application_url, "
                    "detail_scraped_at, detail_error) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (url, job.get("title"), None, short_desc, job.get("location"),
                     site, strategy, now, job.get("posted_at"), full_description, url,
                     detail_scraped_at, detail_error),
                )
                counts["new"] += 1
            except sqlite3.IntegrityError:
                counts["existing"] += 1

    write_with_retry(conn, _do_inserts)
    return counts["new"], counts["existing"]


def _process_one(
    employer_key: str,
    employers: dict,
    search_text: str,
    location_filter: bool,
    accept_locs: list[str],
    reject_locs: list[str],
) -> dict:
    """Search one employer, fetch details, store results."""
    emp = employers[employer_key]

    try:
        jobs = search_employer(
            employer_key, emp, search_text,
            location_filter=location_filter,
            accept_locs=accept_locs,
            reject_locs=reject_locs,
        )
    except WorkdayEmployerFailure as e:
        log.error("%s: Workday search failed (%s)", emp["name"], _exception_summary(e))
        return {
            "employer": emp["name"],
            "employer_key": employer_key,
            "query": search_text,
            "found": 0,
            "new": 0,
            "existing": 0,
            "error": str(e),
            "quarantine": e.quarantine,
        }
    except Exception as e:
        log.error("%s: Workday search failed (%s)", emp["name"], _exception_summary(e))
        return {
            "employer": emp["name"],
            "employer_key": employer_key,
            "query": search_text,
            "found": 0,
            "new": 0,
            "existing": 0,
            "error": str(e),
            "quarantine": False,
        }

    if not jobs:
        return {"employer": emp["name"], "query": search_text,
                "found": 0, "new": 0, "existing": 0}

    try:
        jobs = fetch_details(emp, jobs)
    except Exception as e:
        log.error("%s: Workday detail fetch failed (%s)", emp["name"], _exception_summary(e))

    conn = get_connection()
    new, existing = store_results(conn, jobs, employers)
    log.info("%s: Workday results stored", emp["name"])

    return {"employer": emp["name"], "query": search_text,
            "found": len(jobs), "new": new, "existing": existing}


# -- Main orchestrator -------------------------------------------------------

def scrape_employers(
    search_text: str,
    employers: dict,
    employer_keys: list[str] | None = None,
    location_filter: bool = True,
    max_results: int = 0,
    accept_locs: list[str] | None = None,
    reject_locs: list[str] | None = None,
    workers: int = 1,
) -> dict:
    """Run full scrape: search -> filter -> detail -> store.

    Sequential by default. When workers > 1, processes employers in parallel
    using ThreadPoolExecutor.
    """
    if employer_keys is None:
        employer_keys = list(employers.keys())

    if accept_locs is None:
        accept_locs = []
    if reject_locs is None:
        reject_locs = []

    # Ensure DB schema
    init_db()

    total_new = 0
    total_existing = 0
    total_found = 0
    errors = 0
    quarantined: set[str] = set()

    valid_keys = [k for k in employer_keys if k in employers]

    if workers > 1 and len(valid_keys) > 1:
        # Parallel mode
        completed = 0
        with ThreadPoolExecutor(max_workers=min(workers, len(valid_keys))) as pool:
            futures = {
                pool.submit(
                    _process_one, key, employers, search_text,
                    location_filter, accept_locs, reject_locs,
                ): key
                for key in valid_keys
            }
            for future in as_completed(futures):
                result = future.result()
                completed += 1
                total_new += result["new"]
                total_existing += result["existing"]
                total_found += result["found"]
                if "error" in result:
                    errors += 1
                if result.get("quarantine") and result.get("employer_key"):
                    quarantined.add(result["employer_key"])
    else:
        # Sequential mode (default)
        completed = 0
        for key in valid_keys:
            result = _process_one(
                key, employers, search_text,
                location_filter, accept_locs, reject_locs,
            )
            completed += 1
            total_new += result["new"]
            total_existing += result["existing"]
            total_found += result["found"]
            if "error" in result:
                errors += 1
            if result.get("quarantine") and result.get("employer_key"):
                quarantined.add(result["employer_key"])

    return {
        "found": total_found,
        "new": total_new,
        "existing": total_existing,
        "errors": errors,
        "quarantined": quarantined,
    }


# -- Public entry point ------------------------------------------------------

def run_workday_discovery(employers: dict | None = None, workers: int = 1) -> dict:
    """Main entry point for Workday-based corporate job discovery.

    Loads employer registry from config/employers.yaml (or uses the provided
    dict), then loads search queries from the user's search config to run
    a full crawl across all employers.

    Args:
        employers: Override the employer registry. If None, loads from YAML.
        workers: Number of parallel threads for employer scraping. Default 1 (sequential).

    Returns:
        Dict with stats: found, new, existing, queries.
    """
    if employers is None:
        employers = load_employers()

    if not employers:
        log.warning("No employers configured. Create config/employers.yaml.")
        return {"found": 0, "new": 0, "existing": 0, "queries": 0}

    search_cfg = config.load_search_config()
    queries_cfg = search_cfg.get("queries", [])
    accept_locs, reject_locs = _load_location_filter(search_cfg)

    # Default to tier 1-2 queries for workday scraping
    max_tier = search_cfg.get("workday_max_tier", 2)
    queries = [q["query"] for q in queries_cfg if q.get("tier", 99) <= max_tier]

    if not queries:
        # Fallback: use all queries
        queries = [q["query"] for q in queries_cfg]

    if not queries:
        log.warning("No search queries configured in searches.yaml.")
        return {"found": 0, "new": 0, "existing": 0, "queries": 0}

    proxy = search_cfg.get("proxy")
    if proxy:
        setup_proxy(proxy)

    location_filter = search_cfg.get("workday_location_filter", True)

    log.info("Workday crawl starting")

    grand_new = 0
    grand_existing = 0
    grand_found = 0
    grand_errors = 0
    quarantined_employers: set[str] = set()

    for i, query in enumerate(queries, 1):
        log.info("Running Workday query %d/%d", i, len(queries))
        active_employers = [key for key in employers.keys() if key not in quarantined_employers]
        if not active_employers:
            log.warning("All Workday employers are quarantined; stopping remaining queries.")
            break
        result = scrape_employers(
            search_text=query,
            employers=employers,
            location_filter=location_filter,
            accept_locs=accept_locs,
            reject_locs=reject_locs,
            workers=workers,
            employer_keys=active_employers,
        )
        grand_new += result["new"]
        grand_existing += result["existing"]
        grand_found += result["found"]
        grand_errors += result.get("errors", 0)
        quarantined_employers.update(result.get("quarantined", set()))

    log.info("Workday crawl complete")

    return {
        "found": grand_found,
        "new": grand_new,
        "existing": grand_existing,
        "errors": grand_errors,
        "queries": len(queries),
    }
