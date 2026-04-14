"""Search orchestration."""

__all__ = ["_run_one_search", "search_jobs"]

"""Runner."""

import inspect as _inspect
import logging

from jobspy import scrape_jobs as _raw_scrape_jobs

# Only pass params that the installed jobspy version actually accepts.
_JOBSPY_PARAMS = set(_inspect.signature(_raw_scrape_jobs).parameters.keys())

from applypilot.discovery.jobspy.constants import (
    _HOURS_OLD_SUPPORTED,
)
from applypilot.discovery.jobspy.debug import _emit_debug_log, parse_proxy
from applypilot.discovery.jobspy.scraper import (
    _concat_site_results,
    _scrape_sites_independently,
    _build_site_scrape_kwargs,
    _scrape_with_retry,
)
from applypilot.discovery.jobspy.filters import (
    _resolve_search_distance,
    _resolve_jobspy_sites,
    _location_ok,
    _apply_local_hours_filter,
)
from applypilot.discovery.jobspy.quarantine import _load_site_quarantines
from applypilot.discovery.jobspy.storage import store_jobspy_results

from applypilot.bootstrap import get_app

log = logging.getLogger(__name__)


def _run_one_search(
        search: dict,
        sites: list[str],
        results_per_site: int,
        hours_old: int,
        proxy_config: dict | None,
        defaults: dict,
        max_retries: int,
        accept_locs: list[str],
        reject_locs: list[str],
        glassdoor_map: dict,
        quarantined_sites: dict[str, dict] | None = None,
        loc_mode: str = "include_only",
) -> dict:
    """Run a single search query and store results in DB."""
    s = search
    label = f'"{s["query"]}" in {s["location"]} {"(remote)" if s.get("remote") else ""}'
    if "tier" in s:
        label += f" [tier {s['tier']}]"
    distance = _resolve_search_distance(s, defaults)
    if distance is not None:
        label += f" [{distance}mi]"

    # Split sites: Glassdoor needs simplified location, others use original
    gd_location = glassdoor_map.get(s["location"], s["location"].split(",")[0])
    other_sites, has_glassdoor = _resolve_jobspy_sites(sites, bool(s.get("remote")))

    _emit_debug_log(
        hypothesis_id="H2",
        location="jobspy.py:_run_one_search",
        message="resolved site list for query",
        data={
            "query": s["query"],
            "location": s["location"],
            "remote": bool(s.get("remote")),
            "distance": distance,
            "configured_sites": sites,
            "resolved_non_glassdoor_sites": other_sites,
            "glassdoor_enabled": has_glassdoor,
        },
    )

    all_dfs: list[object] = []

    # Run non-Glassdoor sites independently to preserve partial success when one board fails.
    newly_quarantined: dict[str, dict] = {}
    if other_sites:
        site_dfs, non_gd_failures, newly_quarantined = _scrape_sites_independently(
            label=label,
            search_term=s["query"],
            location=s["location"],
            sites=other_sites,
            results_per_site=results_per_site,
            hours_old=hours_old,
            proxy_config=proxy_config,
            remote_only=bool(s.get("remote")),
            distance=distance,
            country_indeed=defaults.get("country_indeed", "usa"),
            max_retries=max_retries,
            verbose=0,
            hypothesis_id="H1",
            quarantined_sites=quarantined_sites,
        )
        all_dfs.extend(site_dfs)
        if non_gd_failures and all_dfs:
            log.warning(
                "[%s]: partial site success (%d/%d sites failed)",
                label,
                len(non_gd_failures),
                len(other_sites),
            )

    # Run Glassdoor separately with simplified location
    if has_glassdoor:
        gd_kwargs = {
            "site_name": ["glassdoor"],
            "search_term": s["query"],
            "location": gd_location,
            "results_wanted": results_per_site,
            "hours_old": hours_old,
            "description_format": "markdown",
            "verbose": 0,
        }
        if distance is not None:
            gd_kwargs["distance"] = distance
        if s.get("remote"):
            gd_kwargs["is_remote"] = True
        if proxy_config:
            gd_kwargs["proxies"] = [proxy_config["jobspy"]]
        try:
            gd_df = _scrape_with_retry(gd_kwargs, max_retries=max_retries)
            all_dfs.append(gd_df)
        except Exception as e:
            log.error("[%s] (glassdoor): %s", label, e)

    attempted_sites = [site for site in other_sites if not quarantined_sites or site not in quarantined_sites]
    if has_glassdoor:
        attempted_sites.append("glassdoor")
    if not attempted_sites and not all_dfs:
        log.warning("[%s]: all sites skipped because they are quarantined", label)
        return {
            "new": 0,
            "existing": 0,
            "errors": 0,
            "filtered": 0,
            "total": 0,
            "label": label,
            "quarantined_sites": newly_quarantined,
        }

    if not all_dfs:
        log.error("[%s]: all sites failed", label)
        return {
            "new": 0,
            "existing": 0,
            "errors": 1,
            "filtered": 0,
            "total": 0,
            "label": label,
            "quarantined_sites": newly_quarantined,
        }

    df = _concat_site_results(all_dfs)

    if len(df) == 0:
        log.info("[%s] 0 results", label)
        return {"new": 0, "existing": 0, "errors": 0, "filtered": 0, "total": 0, "label": label}

    recency_filtered = 0
    if not _HOURS_OLD_SUPPORTED:
        df, recency_filtered, _ = _apply_local_hours_filter(df, hours_old)

    # Filter by location before storing
    before = len(df)
    df = df[
        df.apply(
            lambda row: _location_ok(
                str(row.get("location", "")) if str(row.get("location", "")) != "nan" else None,
                accept_locs,
                reject_locs,
                loc_mode,
            ),
            axis=1,
        )
    ]
    filtered = before - len(df)

    job_repo = get_app().container.job_repo
    new, existing = store_jobspy_results(job_repo, df, s["query"])

    msg = f"[{label}] {before} results -> {new} new, {existing} dupes"
    if filtered:
        msg += f", {filtered} filtered (location)"
    if recency_filtered:
        msg += f", {recency_filtered} filtered (recency)"
    log.info(msg)

    return {
        "new": new,
        "existing": existing,
        "errors": 0,
        "filtered": filtered + recency_filtered,
        "total": before,
        "label": label,
        "quarantined_sites": newly_quarantined,
    }


def search_jobs(
        query: str,
        location: str,
        sites: list[str] | None = None,
        remote_only: bool = False,
        results_per_site: int = 50,
        hours_old: int = 72,
        proxy: str | None = None,
        country_indeed: str = "usa",
        distance: int | None = None,
) -> dict:
    """Run a single job search via JobSpy and store results in DB."""
    if sites is None:
        sites = ["indeed", "linkedin", "zip_recruiter"]

    proxy_config = parse_proxy(proxy) if proxy else None
    effective_distance = _resolve_search_distance(
        {"remote": remote_only, "distance": distance},
        defaults=None,
    )
    effective_sites, has_glassdoor = _resolve_jobspy_sites(sites, remote_only)
    active_quarantines = _load_site_quarantines()
    preexisting_quarantines = dict(active_quarantines)

    log.info(
        'Search: "%s" in %s | sites=%s | remote=%s | distance=%s',
        query,
        location,
        sites,
        remote_only,
        effective_distance if effective_distance is not None else "default",
    )
    _emit_debug_log(
        hypothesis_id="H5",
        location="jobspy.py:search_jobs",
        message="single-search invocation",
        data={
            "query": query,
            "location": location,
            "sites": sites,
            "remote_only": remote_only,
            "distance": effective_distance,
        },
    )
    _emit_debug_log(
        hypothesis_id="H2",
        location="jobspy.py:search_jobs",
        message="resolved site list for single search",
        data={
            "query": query,
            "location": location,
            "remote_only": remote_only,
            "distance": effective_distance,
            "configured_sites": sites,
            "resolved_non_glassdoor_sites": effective_sites,
            "glassdoor_enabled": has_glassdoor,
        },
    )

    all_dfs: list[object] = []
    site_dfs, failures, newly_quarantined = _scrape_sites_independently(
        label=f'"{query}" in {location} {"(remote)" if remote_only else ""}',
        search_term=query,
        location=location,
        sites=effective_sites,
        results_per_site=results_per_site,
        hours_old=hours_old,
        proxy_config=proxy_config,
        remote_only=remote_only,
        distance=effective_distance,
        country_indeed=country_indeed,
        max_retries=2,
        verbose=2,
        hypothesis_id="H5",
        quarantined_sites=active_quarantines,
    )
    all_dfs.extend(site_dfs)
    active_quarantines.update(newly_quarantined)

    if has_glassdoor:
        gd_kwargs = _build_site_scrape_kwargs(
            site="glassdoor",
            search_term=query,
            location=location,
            results_per_site=results_per_site,
            hours_old=hours_old,
            proxy_config=proxy_config,
            remote_only=remote_only,
            distance=effective_distance,
            country_indeed=country_indeed,
            verbose=2,
        )
        try:
            all_dfs.append(_scrape_with_retry(gd_kwargs, max_retries=2))
        except Exception as e:
            failures.append({"site": "glassdoor", "exception_message": str(e)})
            log.error('["%s" in %s] (glassdoor): %s', query, location, e)

    attempted_sites = [site for site in effective_sites if site not in preexisting_quarantines]
    if has_glassdoor:
        attempted_sites.append("glassdoor")
    if not attempted_sites and not all_dfs:
        log.warning('JobSpy search skipped: all sites are quarantined for "%s" in %s', query, location)
        return {"total": 0, "new": 0, "existing": 0}
    if not all_dfs:
        log.error('JobSpy search failed: all sites failed for "%s" in %s', query, location)
        return {"error": "all sites failed", "total": 0, "new": 0, "existing": 0}
    attempted_sites = len(effective_sites) + (1 if has_glassdoor else 0)
    if failures and all_dfs:
        log.warning(
            '["%s" in %s]: partial site success (%d/%d sites failed)',
            query,
            location,
            len(failures),
            attempted_sites,
        )

    df = _concat_site_results(all_dfs)

    recency_filtered = 0
    if not _HOURS_OLD_SUPPORTED:
        df, recency_filtered, _ = _apply_local_hours_filter(df, hours_old)
        if recency_filtered:
            log.info("JobSpy recency filter removed %d stale results locally", recency_filtered)

    total = len(df)
    log.info("JobSpy returned %d results", total)

    if total == 0:
        return {"total": 0, "new": 0, "existing": 0}

    if "site" in df.columns:
        site_counts = df["site"].value_counts()
        for site, count in site_counts.items():
            log.info("  %s: %d", site, count)

    container = get_app().container
    new, existing = store_jobspy_results(container.job_repo, df, query)
    log.info("Stored: %d new, %d already in DB", new, existing)

    counts = container.job_repo.get_pipeline_counts()
    log.info("DB total: %d jobs, %d pending detail scrape", counts["total"], counts["total"] - counts["with_desc"])

    return {"total": total, "new": new, "existing": existing}
