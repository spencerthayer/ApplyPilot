"""Search."""

__all__ = [
    "log",
    "GREENHOUSE_API_BASE",
    "_validate_employer_registry",
    "load_employers",
    "_load_location_filter",
    "_location_ok",
    "_title_matches_query",
    "search_employer",
    "search_all",
    "run_all_searches",
]

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml

from applypilot import config
from applypilot.config import APP_DIR, CONFIG_DIR

log = logging.getLogger(__name__)

# Greenhouse Job Board API endpoint
GREENHOUSE_API_BASE = "https://boards-api.greenhouse.io/v1/boards"

from applypilot.discovery.greenhouse.api import fetch_jobs_api, parse_api_response
from applypilot.discovery.greenhouse.storage import _store_jobs


def _validate_employer_registry(data: dict, source: str) -> dict:
    """Validate the parsed Greenhouse registry shape."""
    if not isinstance(data, dict):
        raise ValueError(f"Invalid Greenhouse config at {source}: expected a mapping at the top level.")

    extra_keys = sorted(key for key in data.keys() if key != "employers")
    if extra_keys:
        joined = ", ".join(extra_keys[:5])
        raise ValueError(
            f"Invalid Greenhouse config at {source}: unexpected top-level keys outside 'employers' ({joined})."
        )

    employers = data.get("employers")
    if not isinstance(employers, dict):
        raise ValueError(f"Invalid Greenhouse config at {source}: 'employers' must be a mapping.")

    for employer_key, employer in employers.items():
        if not isinstance(employer, dict):
            raise ValueError(f"Invalid Greenhouse config at {source}: employer '{employer_key}' must map to an object.")
        if not str(employer.get("name", "")).strip():
            raise ValueError(
                f"Invalid Greenhouse config at {source}: employer '{employer_key}' is missing a non-empty name."
            )

    return employers


def load_employers() -> dict:
    """Load Greenhouse employer registry.

    Tries user config first (~/.applypilot/greenhouse.yaml),
    falls back to package config.
    """
    # Try user config
    user_path = APP_DIR / "greenhouse.yaml"
    if user_path.exists():
        log.info("Loading user Greenhouse config from %s", user_path)
        data = yaml.safe_load(user_path.read_text(encoding="utf-8"))
        return _validate_employer_registry(data or {}, str(user_path))

    # Fall back to package config
    package_path = CONFIG_DIR / "greenhouse.yaml"
    if not package_path.exists():
        log.warning("greenhouse.yaml not found at %s", package_path)
        return {}

    data = yaml.safe_load(package_path.read_text(encoding="utf-8"))
    return _validate_employer_registry(data or {}, str(package_path))


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


def _title_matches_query(title: str, query: str, *, strict: bool = False) -> bool:
    """Check if job title matches search query — delegates to shared filter."""
    from applypilot.discovery.title_filter import title_matches_query

    return title_matches_query(title, query, strict=strict)


def search_employer(
        employer_key: str,
        employer: dict,
        search_text: str,
        location_filter: bool = True,
        accept_locs: list[str] | None = None,
        reject_locs: list[str] | None = None,
        strict_title: bool = False,
        queries: list[str] | None = None,
) -> list[dict]:
    """Search a single Greenhouse employer via API."""
    log.info("%s: starting Greenhouse search", employer["name"])

    # Fetch from API
    api_data = fetch_jobs_api(employer_key, content=True)
    if not api_data:
        return []

    # Get total job count before title filtering (for pollution detection)
    total_from_api = len(api_data.get("jobs", []))
    jobs = parse_api_response(api_data, employer["name"], search_text)

    # Additional title filter against search queries (when search_text is empty)
    if queries and not search_text:
        before = len(jobs)
        jobs = [
            j for j in jobs
            if any(_title_matches_query(j.get("title", ""), q, strict=strict_title) for q in queries)
        ]
        if before > 0 and len(jobs) == 0:
            log.warning(
                "No relevant results from %s — portal may be returning suggestions (got %d, all filtered by title)",
                employer["name"],
                before,
            )

    # Apply location filter
    if location_filter and (accept_locs or reject_locs):
        jobs = [j for j in jobs if _location_ok(j.get("location"), accept_locs or [], reject_locs or [])]

    # Pollution detection: API returned results but all were filtered
    if total_from_api > 0 and len(jobs) == 0 and not queries:
        log.warning(
            "No relevant results from %s — portal may be returning suggestions (got %d, all filtered by title)",
            employer["name"],
            total_from_api,
        )

    log.info("%s: Greenhouse search complete", employer["name"])
    return jobs


def search_all(
        search_text: str,
        workers: int = 4,
        location_filter: bool = True,
        _employers_override: dict | None = None,
        employer_keys: list[str] | None = None,
        strict_title: bool = False,
) -> tuple[int, int]:
    """Search all configured Greenhouse employers via API.

    Returns (new_jobs_count, existing_jobs_count).
    """
    employers = _employers_override if _employers_override else load_employers()
    if not employers:
        log.warning("No Greenhouse employers configured")
        return 0, 0

    # Filter by registry-resolved keys
    if employer_keys is not None:
        employers = {k: v for k, v in employers.items() if k in employer_keys}
        if not employers:
            log.info("No Greenhouse employers match requested companies")
            return 0, 0
        log.info("Greenhouse: filtered to %d employers by --company", len(employers))

    accept_locs, reject_locs = _load_location_filter()

    log.info("Greenhouse API search starting")

    all_jobs = []
    errors = 0

    # Load search queries for title filtering
    search_cfg = config.load_search_config()
    queries = [q["query"] for q in search_cfg.get("queries", [])]

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                search_employer,
                key,
                emp,
                search_text,
                location_filter,
                accept_locs,
                reject_locs,
                strict_title,
                queries,
            ): key
            for key, emp in employers.items()
        }

        for future in as_completed(futures):
            key = futures[future]
            try:
                jobs = future.result()
                all_jobs.extend(jobs)
            except Exception as e:
                log.error(
                    "Greenhouse employer search failed for %s (%s)",
                    key,
                    __import__(
                        "applypilot.discovery.smartextract.pipeline", fromlist=["_exception_summary"]
                    )._exception_summary(e),
                )
                errors += 1

    log.info("Greenhouse API search complete")

    # Store in database
    return _store_jobs(all_jobs)


def run_all_searches(
        searches: list[dict],
        workers: int = 4,
) -> dict:
    """Run multiple search queries across all Greenhouse employers.

    Args:
        searches: List of search configs with 'query' key
        workers: Number of parallel threads

    Returns:
        Dict with total new/existing counts and per-query breakdown
    """
    total_new = 0
    total_existing = 0
    per_query = []

    for search in searches:
        query = search.get("query", "")
        log.info("Running Greenhouse API query")

        new, existing = search_all(query, workers=workers)
        total_new += new
        total_existing += existing

        per_query.append(
            {
                "query": query,
                "new": new,
                "existing": existing,
            }
        )

    return {
        "total_new": total_new,
        "total_existing": total_existing,
        "per_query": per_query,
    }
