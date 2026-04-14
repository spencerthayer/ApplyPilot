"""Employer."""

import logging
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser

import yaml

from applypilot import config
from applypilot.config import CONFIG_DIR

log = logging.getLogger(__name__)
_QUARANTINE_HTTP_STATUSES = {401, 404, 422}

from applypilot.discovery.workday.api import workday_search, workday_detail


def load_employers() -> dict:
    """Load Workday employer registry from config/employers.yaml."""
    path = CONFIG_DIR / "employers.yaml"
    if not path.exists():
        log.warning("employers.yaml not found at %s", path)
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("employers", {})


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


def search_employer(
        employer_key: str,
        employer: dict,
        search_text: str,
        location_filter: bool = True,
        max_results: int = 0,
        accept_locs: list[str] | None = None,
        reject_locs: list[str] | None = None,
        strict_title: bool = False,
) -> list[dict]:
    """Search an employer, paginate through all results, optionally filter by location."""
    log.info("%s: starting Workday search", employer["name"])

    all_jobs: list[dict] = []
    offset = 0
    page_size = 20
    max_pages = 25  # Cap at 500 results
    total = None

    while True:
        try:
            data = workday_search(employer, search_text, limit=page_size, offset=offset)
        except urllib.error.HTTPError as e:
            message = f"HTTP Error {e.code}: {e.reason}"
            if offset == 0:
                quarantine = e.code in _QUARANTINE_HTTP_STATUSES
                log.error("%s: API error at offset %d (%s)", employer["name"], offset, message)
                raise WorkdayEmployerFailure(message, quarantine=quarantine) from e
            log.error("%s: API error at offset %d (%s)", employer["name"], offset, message)
            break
        except Exception as e:
            if offset == 0:
                log.error("%s: API error at offset %d (%s)", employer["name"], offset, _exception_summary(e))
                raise WorkdayEmployerFailure(str(e)) from e
            log.error("%s: API error at offset %d (%s)", employer["name"], offset, _exception_summary(e))
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

            title = j.get("title", "")
            # Title relevance filter
            if title and search_text:
                from applypilot.discovery.title_filter import title_matches_query

                if not title_matches_query(title, search_text, strict=strict_title):
                    continue

            all_jobs.append(
                {
                    "title": j.get("title", ""),
                    "location": loc,
                    "posted": j.get("postedOn", ""),
                    "external_path": j.get("externalPath", ""),
                    "employer_key": employer_key,
                    "employer_name": employer["name"],
                }
            )

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
