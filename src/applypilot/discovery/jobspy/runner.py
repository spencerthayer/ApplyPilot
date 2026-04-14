"""Discovery runner."""

"""Runner."""

import inspect as _inspect
import logging
from datetime import timezone

from jobspy import scrape_jobs as _raw_scrape_jobs

# Only pass params that the installed jobspy version actually accepts.
_JOBSPY_PARAMS = set(_inspect.signature(_raw_scrape_jobs).parameters.keys())

from applypilot.discovery.jobspy.constants import (
    _DEBUG_JOBSPY_ENABLED,
)
from applypilot.discovery.jobspy.debug import _emit_debug_log, _jobspy_debug_compat_snapshot, parse_proxy
from applypilot.discovery.jobspy.filters import _load_location_config
from applypilot.discovery.jobspy.quarantine import _load_site_quarantines

from applypilot.discovery.jobspy.search import _run_one_search

from applypilot import config
from applypilot.bootstrap import get_app

log = logging.getLogger(__name__)


def _full_crawl(
        search_cfg: dict,
        tiers: list[int] | None = None,
        locations: list[str] | None = None,
        sites: list[str] | None = None,
        results_per_site: int = 100,
        hours_old: int = 72,
        proxy: str | None = None,
        max_retries: int = 2,
) -> dict:
    """Run all search queries from search config across all locations."""
    if sites is None:
        sites = ["indeed", "linkedin", "zip_recruiter"]

    # Build search combinations from config
    queries = search_cfg.get("queries", [])
    locs = search_cfg.get("locations", [])
    defaults = search_cfg.get("defaults", {})
    glassdoor_map = search_cfg.get("glassdoor_location_map", {})
    accept_locs, reject_locs, loc_mode = _load_location_config(search_cfg)

    if tiers:
        queries = [q for q in queries if q.get("tier") in tiers]
    if locations:
        locs = [loc for loc in locs if loc.get("label") in locations]

    searches = []
    for q in queries:
        for loc in locs:
            searches.append(
                {
                    "query": q["query"],
                    "location": loc["location"],
                    "remote": loc.get("remote", False),
                    "distance": loc.get("distance", defaults.get("distance")),
                    "tier": q.get("tier", 0),
                }
            )

    proxy_config = parse_proxy(proxy) if proxy else None
    active_quarantines = _load_site_quarantines()

    log.info("Full crawl: %d search combinations", len(searches))
    log.info("Sites: %s | Results/site: %d | Hours old: %d", ", ".join(sites), results_per_site, hours_old)
    log.debug("[discover] queries: %s", [s.get("query") or s.get("search_term") for s in searches[:10]])
    if active_quarantines:
        for site, info in active_quarantines.items():
            until = info["until"].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            log.warning("JobSpy site quarantine active: %s skipped until %s (%s)", site, until, info["reason"])
    if _DEBUG_JOBSPY_ENABLED:
        compat = _jobspy_debug_compat_snapshot()
        log.info(
            "JobSpy debug mode: version=%s | hours_old_supported=%s | site_by_site_fallback=%s",
            compat["python_jobspy_version"],
            compat["hours_old_supported"],
            compat["site_by_site_fallback_enabled"],
        )
        _emit_debug_log(
            hypothesis_id="H3",
            location="jobspy.py:_full_crawl",
            message="jobspy runtime compatibility snapshot",
            data=compat,
        )

    # Ensure DB schema is ready
    get_app()  # bootstrap handles init_db via Container

    total_new = 0
    total_existing = 0
    total_errors = 0
    completed = 0

    for s in searches:
        result = _run_one_search(
            s,
            sites,
            results_per_site,
            hours_old,
            proxy_config,
            defaults,
            max_retries,
            accept_locs,
            reject_locs,
            glassdoor_map,
            active_quarantines,
            loc_mode,
        )
        completed += 1
        total_new += result["new"]
        total_existing += result["existing"]
        total_errors += result["errors"]
        active_quarantines.update(result.get("quarantined_sites", {}))

        if completed % 5 == 0 or completed == len(searches):
            log.info(
                "Progress: %d/%d queries done (%d new, %d dupes, %d errors)",
                completed,
                len(searches),
                total_new,
                total_existing,
                total_errors,
            )

    # Final stats
    db_total = get_app().container.job_repo.get_pipeline_counts()["total"]

    log.info(
        "Full crawl complete: %d new | %d dupes | %d errors | %d total in DB",
        total_new,
        total_existing,
        total_errors,
        db_total,
    )

    return {
        "new": total_new,
        "existing": total_existing,
        "errors": total_errors,
        "db_total": db_total,
        "queries": len(searches),
    }


def run_discovery(cfg: dict | None = None, sites_override: list[str] | None = None, company_records=None,
                  strict_title: bool = False) -> dict:
    """Main entry point for JobSpy-based job discovery.

    Loads search queries and locations from the user's search config YAML,
    then runs a full crawl across all configured job boards.

    Args:
        cfg: Override the search configuration dict. If None, loads from
             the user's searches.yaml file.
        sites_override: If provided, use these sites instead of the config's
             sites key. Used to run a specific board (e.g. ["dice"]).

    Returns:
        Dict with stats: new, existing, errors, db_total, queries.
    """
    if cfg is None:
        cfg = config.load_search_config()

    if not cfg:
        log.warning("No search configuration found. Run `applypilot init` to create one.")
        return {"new": 0, "existing": 0, "errors": 0, "db_total": 0, "queries": 0}

    proxy = cfg.get("proxy")
    sites = sites_override or cfg.get("sites")
    results_per_site = cfg.get("defaults", {}).get("results_per_site", 100)
    hours_old = cfg.get("defaults", {}).get("hours_old", 72)
    tiers = cfg.get("tiers")
    locations = cfg.get("location_labels")

    return _full_crawl(
        search_cfg=cfg,
        tiers=tiers,
        locations=locations,
        sites=sites,
        results_per_site=results_per_site,
        hours_old=hours_old,
        proxy=proxy,
    )
