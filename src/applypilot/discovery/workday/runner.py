"""Runner."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from applypilot import config
from applypilot.bootstrap import get_app

log = logging.getLogger(__name__)
_QUARANTINE_HTTP_STATUSES = {401, 404, 422}

from applypilot.discovery.workday.employer import (
    load_employers,
    _load_location_filter,
    search_employer,
    WorkdayEmployerFailure,
    fetch_details,
    _exception_summary,
)
from applypilot.discovery.workday.api import setup_proxy
from applypilot.discovery.workday.storage import store_results


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
            employer_key,
            emp,
            search_text,
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
        return {"employer": emp["name"], "query": search_text, "found": 0, "new": 0, "existing": 0}

    try:
        jobs = fetch_details(emp, jobs)
    except Exception as e:
        log.error("%s: Workday detail fetch failed (%s)", emp["name"], _exception_summary(e))

    new, existing = store_results(jobs, employers)
    log.info("%s: Workday results stored", emp["name"])

    return {"employer": emp["name"], "query": search_text, "found": len(jobs), "new": new, "existing": existing}


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
    get_app()

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
                    _process_one,
                    key,
                    employers,
                    search_text,
                    location_filter,
                    accept_locs,
                    reject_locs,
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
                key,
                employers,
                search_text,
                location_filter,
                accept_locs,
                reject_locs,
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


def run_workday_discovery(
        employers: dict | None = None, workers: int = 1, employer_keys: list[str] | None = None,
        strict_title: bool = False
) -> dict:
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

    # Filter by registry-resolved keys
    if employer_keys is not None:
        employers = {k: v for k, v in employers.items() if k in employer_keys}
        if not employers:
            log.info("No Workday employers match requested companies")
            return {"found": 0, "new": 0, "existing": 0, "queries": 0}
        log.info("Workday: filtered to %d employers by --company", len(employers))

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
