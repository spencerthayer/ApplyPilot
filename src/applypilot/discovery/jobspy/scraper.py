"""Scraper."""

__all__ = [
    "_JOBSPY_PARAMS",
    "scrape_jobs",
    "_scrape_with_retry",
    "_normalize_scrape_kwargs",
    "_scrape_sites_independently",
    "_build_site_scrape_kwargs",
    "_concat_site_results",
]

"""JobSpy-based job discovery: searches Indeed, LinkedIn, Glassdoor, ZipRecruiter.

Uses python-jobspy to scrape multiple job boards, deduplicates results,
parses salary ranges, and stores everything in the ApplyPilot database.

Search queries, locations, and filtering rules are loaded from the user's
search configuration YAML (searches.yaml) rather than being hardcoded.
"""

import inspect as _inspect
import logging

log = logging.getLogger(__name__)
import time
import warnings
from datetime import timezone

import pandas as pd
from jobspy import scrape_jobs as _raw_scrape_jobs

# Only pass params that the installed jobspy version actually accepts.
_JOBSPY_PARAMS = set(_inspect.signature(_raw_scrape_jobs).parameters.keys())

from applypilot.discovery.jobspy.constants import (
    _SCRAPE_JOBS_PARAMS,
    _HOURS_OLD_SUPPORTED,
)
from applypilot.discovery.jobspy.debug import _emit_debug_log, _warn_hours_old_fallback
from applypilot.discovery.jobspy.quarantine import _quarantine_reason_for_exception, _quarantine_site
from applypilot.discovery.jobspy.ziprecruiter import _scrape_ziprecruiter_browser


def scrape_jobs(**kwargs):
    """Wrapper that filters kwargs to only what the installed jobspy supports."""
    filtered = {k: v for k, v in kwargs.items() if k in _JOBSPY_PARAMS}
    return _raw_scrape_jobs(**filtered)


def _scrape_with_retry(kwargs: dict, max_retries: int = 2, backoff: float = 5.0):
    """Call scrape_jobs with retry on transient failures."""
    normalized_kwargs = _normalize_scrape_kwargs(kwargs)
    for attempt in range(max_retries + 1):
        try:
            _emit_debug_log(
                hypothesis_id="H4",
                location="jobspy.py:_scrape_with_retry",
                message="scrape attempt start",
                data={
                    "attempt": attempt,
                    "max_retries": max_retries,
                    "site_name": normalized_kwargs.get("site_name"),
                },
            )
            return scrape_jobs(**normalized_kwargs)
        except Exception as e:
            err = str(e).lower()
            transient = any(k in err for k in ("timeout", "429", "proxy", "connection", "reset", "refused"))
            _emit_debug_log(
                hypothesis_id="H4",
                location="jobspy.py:_scrape_with_retry",
                message="scrape attempt failed",
                data={
                    "attempt": attempt,
                    "max_retries": max_retries,
                    "site_name": normalized_kwargs.get("site_name"),
                    "exception_class": e.__class__.__name__,
                    "exception_message": str(e),
                    "classified_transient": transient,
                },
            )
            if transient and attempt < max_retries:
                wait = backoff * (attempt + 1)
                log.warning("Retry %d/%d in %.0fs: %s", attempt + 1, max_retries, wait, e)
                time.sleep(wait)
            else:
                raise


def _normalize_scrape_kwargs(kwargs: dict) -> dict:
    """Adapt ApplyPilot kwargs to the installed JobSpy signature."""
    normalized: dict = {}

    for key, value in kwargs.items():
        if key == "proxies":
            if "proxies" in _SCRAPE_JOBS_PARAMS:
                normalized["proxies"] = value
            elif "proxy" in _SCRAPE_JOBS_PARAMS:
                if isinstance(value, list):
                    normalized["proxy"] = value[0] if value else None
                else:
                    normalized["proxy"] = value
            continue

        if key in _SCRAPE_JOBS_PARAMS:
            normalized[key] = value

    if "hours_old" in kwargs and not _HOURS_OLD_SUPPORTED:
        _warn_hours_old_fallback()

    _emit_debug_log(
        hypothesis_id="H2",
        location="jobspy.py:_normalize_scrape_kwargs",
        message="normalized scrape kwargs",
        data={
            "input_site_name": kwargs.get("site_name"),
            "normalized_site_name": normalized.get("site_name"),
            "input_keys": sorted(kwargs.keys()),
            "normalized_keys": sorted(normalized.keys()),
        },
    )

    _emit_debug_log(
        hypothesis_id="H3",
        location="jobspy.py:_normalize_scrape_kwargs",
        message="jobspy compatibility status",
        data={
            "hours_old_supported": _HOURS_OLD_SUPPORTED,
            "signature_has_hours_old": "hours_old" in _SCRAPE_JOBS_PARAMS,
            "signature_params": sorted(_SCRAPE_JOBS_PARAMS),
        },
    )

    return normalized


def _scrape_sites_independently(
        *,
        label: str,
        search_term: str,
        location: str,
        sites: list[str],
        results_per_site: int,
        hours_old: int,
        proxy_config: dict | None,
        remote_only: bool,
        distance: int | None,
        country_indeed: str,
        max_retries: int,
        verbose: int = 0,
        hypothesis_id: str = "H1",
        quarantined_sites: dict[str, dict] | None = None,
) -> tuple[list[object], list[dict], dict[str, dict]]:
    dataframes: list[object] = []
    failures: list[dict] = []
    newly_quarantined: dict[str, dict] = {}
    active_quarantines = quarantined_sites or {}

    for site in sites:
        existing_quarantine = active_quarantines.get(site)
        if existing_quarantine is not None:
            until = existing_quarantine["until"].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            log.warning("[%s] (site=%s): skipped while quarantined until %s", label, site, until)
            continue

        started = time.time()
        try:
            if site == "zip_recruiter":
                df = _scrape_ziprecruiter_browser(
                    search_term=search_term,
                    location=location,
                    results_wanted=results_per_site,
                    remote_only=remote_only,
                    distance=distance,
                    proxy_config=proxy_config,
                )
            else:
                kwargs = _build_site_scrape_kwargs(
                    site=site,
                    search_term=search_term,
                    location=location,
                    results_per_site=results_per_site,
                    hours_old=hours_old,
                    proxy_config=proxy_config,
                    remote_only=remote_only,
                    distance=distance,
                    country_indeed=country_indeed,
                    verbose=verbose,
                )
                df = _scrape_with_retry(kwargs, max_retries=max_retries)
            dataframes.append(df)
            _emit_debug_log(
                hypothesis_id=hypothesis_id,
                location="jobspy.py:_scrape_sites_independently",
                message="site scrape completed",
                data={
                    "query": search_term,
                    "site": site,
                    "elapsed_ms": int((time.time() - started) * 1000),
                    "rows": len(df) if hasattr(df, "__len__") else None,
                },
            )
        except Exception as e:
            elapsed_ms = int((time.time() - started) * 1000)
            if site == "zip_recruiter":
                normalized_kwargs = {
                    "site_name": [site],
                    "search_term": search_term,
                    "location": location,
                    "results_wanted": results_per_site,
                    "is_remote": remote_only,
                    "distance": distance,
                    "strategy": "browser",
                }
            else:
                normalized_kwargs = _normalize_scrape_kwargs(kwargs)
            failure = {
                "site": site,
                "exception_class": e.__class__.__name__,
                "exception_message": str(e),
                "normalized_kwargs": normalized_kwargs,
                "elapsed_ms": elapsed_ms,
            }
            failures.append(failure)
            log.error(
                "[%s] (site=%s): %s: %s | kwargs=%s | elapsed_ms=%d",
                label,
                site,
                e.__class__.__name__,
                e,
                normalized_kwargs,
                elapsed_ms,
            )
            _emit_debug_log(
                hypothesis_id=hypothesis_id,
                location="jobspy.py:_scrape_sites_independently",
                message="site scrape failed",
                data={"query": search_term, **failure},
            )
            quarantine_reason = _quarantine_reason_for_exception(site, e)
            if quarantine_reason is not None:
                info = _quarantine_site(site, quarantine_reason)
                newly_quarantined[site] = info
                until = info["until"].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                log.warning(
                    "[%s] (site=%s): quarantined until %s after %s",
                    label,
                    site,
                    until,
                    quarantine_reason,
                )

    return dataframes, failures, newly_quarantined


def _build_site_scrape_kwargs(
        *,
        site: str,
        search_term: str,
        location: str,
        results_per_site: int,
        hours_old: int,
        proxy_config: dict | None,
        remote_only: bool,
        distance: int | None,
        country_indeed: str,
        verbose: int,
) -> dict:
    kwargs = {
        "site_name": [site],
        "search_term": search_term,
        "location": location,
        "results_wanted": results_per_site,
        "hours_old": hours_old,
        "description_format": "markdown",
        "verbose": verbose,
    }
    if distance is not None:
        kwargs["distance"] = distance
    if site == "indeed":
        kwargs["country_indeed"] = country_indeed
    if remote_only:
        kwargs["is_remote"] = True
    if proxy_config:
        kwargs["proxies"] = [proxy_config["jobspy"]]
    if site == "linkedin":
        kwargs["linkedin_fetch_description"] = True
    return kwargs


def _concat_site_results(dataframes: list[object]):
    if not dataframes:
        return pd.DataFrame()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        return pd.concat(dataframes, ignore_index=True) if len(dataframes) > 1 else dataframes[0]
