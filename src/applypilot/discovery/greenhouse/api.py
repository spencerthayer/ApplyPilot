"""Api."""

__all__ = [
    "log",
    "GREENHOUSE_API_BASE",
    "_exception_summary",
    "_strip_html",
    "fetch_jobs_api",
    "parse_api_response",
    "fetch_greenhouse_board",
    "parse_greenhouse_jobs",
]

import logging
import re
import time

import httpx

log = logging.getLogger(__name__)

# Greenhouse Job Board API endpoint
GREENHOUSE_API_BASE = "https://boards-api.greenhouse.io/v1/boards"


def _exception_summary(exc: Exception) -> str:
    """Return a minimal exception summary safe for logs."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{exc.__class__.__name__}(status={exc.response.status_code})"
    return exc.__class__.__name__


def _strip_html(html_content: str) -> str:
    """Strip HTML tags from content to get plain text."""
    if not html_content:
        return ""

    # Simple regex to remove HTML tags
    text = re.sub(r"<[^>]+>", "", html_content)
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_jobs_api(board_token: str, content: bool = True) -> dict | None:
    """Fetch jobs from Greenhouse Job Board API.

    Args:
        board_token: The company slug (e.g., "stripe", "robinhood")
        content: If True, include full job description in response

    Returns:
        API response dict with "jobs" and "meta" keys, or None on error
    """
    url = f"{GREENHOUSE_API_BASE}/{board_token}/jobs"
    params = {"content": "true"} if content else {}

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }

    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            resp = client.get(url, headers=headers, params=params)

            if resp.status_code == 404:
                log.debug("Greenhouse board not found")
                return None
            elif resp.status_code == 422:
                log.debug("Greenhouse 422 unprocessable — skipping")
                return None
            elif resp.status_code == 429:
                log.warning("Greenhouse API rate limited; retrying")
                time.sleep(2)
                resp = client.get(url, headers=headers, params=params)
                resp.raise_for_status()
            else:
                resp.raise_for_status()

            return resp.json()

    except httpx.HTTPStatusError as e:
        log.warning("Greenhouse API HTTP error (%s)", _exception_summary(e))
        return None
    except Exception as e:
        log.warning("Greenhouse fetch failed (%s)", _exception_summary(e))
        return None


def parse_api_response(data: dict, company_name: str, query: str = "") -> list[dict]:
    """Parse job listings from Greenhouse API response.

    Args:
        data: API response dict with "jobs" key
        company_name: Display name of the company
        query: Optional query string to filter jobs

    Returns:
        List of job dicts with standardized fields
    """
    jobs = []
    job_list = data.get("jobs", [])

    for job_data in job_list:
        try:
            title = job_data.get("title", "")
            if not title:
                continue

            # Filter by query
            if query:
                from applypilot.discovery.greenhouse.search import _title_matches_query

                if not _title_matches_query(title, query):
                    continue

            # Extract location
            location_obj = job_data.get("location", {})
            location = location_obj.get("name", "") if isinstance(location_obj, dict) else str(location_obj)

            # Extract department
            departments = job_data.get("departments", [])
            department = departments[0].get("name", "") if departments else ""

            # Extract offices
            offices = job_data.get("offices", [])
            office_names = [office.get("name", "") for office in offices if office.get("name")]

            # Get full description and strip HTML
            html_content = job_data.get("content", "")
            description = _strip_html(html_content)

            # Build job dict
            job = {
                "title": title,
                "company": company_name,
                "location": location,
                "department": department,
                "offices": office_names,
                "url": job_data.get("absolute_url", ""),
                "strategy": "greenhouse",
                # New fields from API
                "job_id": job_data.get("id"),
                "internal_job_id": job_data.get("internal_job_id"),
                "description": description,
                "updated_at": job_data.get("updated_at"),
            }

            jobs.append(job)

        except Exception:
            log.debug("Skipping malformed Greenhouse job payload")
            continue

    return jobs


def fetch_greenhouse_board(company_slug: str) -> str | None:
    """DEPRECATED: Use fetch_jobs_api() instead."""
    log.warning("fetch_greenhouse_board() is deprecated, use fetch_jobs_api()")
    return None


def parse_greenhouse_jobs(html: str, company_name: str, query: str = "") -> list[dict]:
    """DEPRECATED: Use parse_api_response() instead."""
    log.warning("parse_greenhouse_jobs() is deprecated, use parse_api_response()")
    return []
