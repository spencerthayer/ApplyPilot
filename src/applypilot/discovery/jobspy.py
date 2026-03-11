"""JobSpy-based job discovery: searches Indeed, LinkedIn, Glassdoor, ZipRecruiter.

Uses python-jobspy to scrape multiple job boards, deduplicates results,
parses salary ranges, and stores everything in the ApplyPilot database.

Search queries, locations, and filtering rules are loaded from the user's
search configuration YAML (searches.yaml) rather than being hardcoded.
"""

import logging
import inspect
import json
import os
import sqlite3
import time
import warnings
from importlib import metadata as importlib_metadata
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd
from jobspy import scrape_jobs

# Patch TLSRotating to always specify a client_identifier.
# Without one, the tls-client Go binary receives a nil JA3 string and panics
# (SIGABRT), killing the process. A named profile avoids the crash.
try:
    import tls_client

    _orig_tls_init = tls_client.Session.__init__

    def _safe_tls_init(self, *args, **kwargs):
        if not kwargs.get("client_identifier") and not kwargs.get("ja3_string"):
            kwargs["client_identifier"] = "chrome_120"
        _orig_tls_init(self, *args, **kwargs)

    tls_client.Session.__init__ = _safe_tls_init
except Exception:
    pass  # If tls_client isn't available, jobspy will use regular requests

# Patch Country.from_string to return WORLDWIDE for unsupported country strings
# (e.g. "sri lanka") instead of raising ValueError that kills the entire scrape.
try:
    from jobspy.model import Country as _Country

    _orig_country_from_string = _Country.from_string.__func__

    @classmethod  # type: ignore[misc]
    def _safe_country_from_string(cls, country_str: str):
        try:
            return _orig_country_from_string(cls, country_str)
        except ValueError:
            return cls.WORLDWIDE

    _Country.from_string = _safe_country_from_string
except Exception:
    pass  # If patching fails, fall back to original behavior

from applypilot import config
from applypilot.database import commit_with_retry, get_connection, init_db

log = logging.getLogger(__name__)
_SCRAPE_JOBS_PARAMS = set(inspect.signature(scrape_jobs).parameters)
_HOURS_OLD_SUPPORTED = "hours_old" in _SCRAPE_JOBS_PARAMS
_HOURS_OLD_WARNING_EMITTED = False
_DEBUG_JOBSPY_ENABLED = os.getenv("APPLYPILOT_DEBUG_JOBSPY") == "1"
_DEBUG_LOG_PATH = Path(os.getenv("APPLYPILOT_DEBUG_LOG_PATH", ".cursor/debug-e0a421.log")).expanduser()
_DEBUG_SESSION_ID = os.getenv("APPLYPILOT_DEBUG_SESSION_ID", "e0a421")
_DEBUG_RUN_ID = os.getenv("APPLYPILOT_DEBUG_RUN_ID", "default")


def _resolve_debug_log_path() -> Path:
    path = _DEBUG_LOG_PATH
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _emit_debug_log(
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict,
    run_id: str = _DEBUG_RUN_ID,
) -> None:
    if not _DEBUG_JOBSPY_ENABLED:
        return
    log_path = _resolve_debug_log_path()
    payload = {
        "sessionId": _DEBUG_SESSION_ID,
        "id": f"log_{int(time.time() * 1000)}_{uuid4().hex[:8]}",
        "timestamp": int(time.time() * 1000),
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except Exception:
        pass


def _jobspy_debug_compat_snapshot() -> dict:
    try:
        version = importlib_metadata.version("python-jobspy")
    except importlib_metadata.PackageNotFoundError:
        version = "unknown"
    return {
        "python_jobspy_version": version,
        "scrape_jobs_signature_params": sorted(_SCRAPE_JOBS_PARAMS),
        "hours_old_supported": _HOURS_OLD_SUPPORTED,
        "site_by_site_fallback_enabled": True,
    }


def _clean(val) -> str | None:
    if val is None:
        return None
    s = str(val)
    return s if s and s != "nan" else None


# -- Proxy parsing -----------------------------------------------------------


def parse_proxy(proxy_str: str) -> dict:
    """Parse host:port:user:pass into components."""
    parts = proxy_str.split(":")
    if len(parts) == 4:
        host, port, user, passwd = parts
        return {
            "host": host,
            "port": port,
            "user": user,
            "pass": passwd,
            "jobspy": f"{user}:{passwd}@{host}:{port}",
            "playwright": {
                "server": f"http://{host}:{port}",
                "username": user,
                "password": passwd,
            },
        }
    elif len(parts) == 2:
        host, port = parts
        return {
            "host": host,
            "port": port,
            "user": None,
            "pass": None,
            "jobspy": f"{host}:{port}",
            "playwright": {"server": f"http://{host}:{port}"},
        }
    else:
        raise ValueError(f"Proxy format not recognized: {proxy_str}. Expected: host:port:user:pass or host:port")


# -- Retry wrapper -----------------------------------------------------------


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


def _warn_hours_old_fallback() -> None:
    global _HOURS_OLD_WARNING_EMITTED
    if _HOURS_OLD_WARNING_EMITTED:
        return
    _HOURS_OLD_WARNING_EMITTED = True
    log.warning(
        "Installed JobSpy does not support server-side hours_old filtering; applying best-effort local recency checks."
    )


def _coerce_posted_datetime(value) -> datetime | None:
    """Convert a JobSpy date_posted cell into a timezone-aware datetime."""
    if value is None:
        return None

    try:
        if hasattr(value, "to_pydatetime"):
            value = value.to_pydatetime()
    except Exception:
        pass

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, dtime.min)
    else:
        raw = str(value).strip()
        if not raw or raw == "nan":
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y"):
                try:
                    dt = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _apply_local_hours_filter(df, hours_old: int) -> tuple[object, int, bool]:
    """Apply a best-effort local recency filter using the date_posted column."""
    if hours_old <= 0 or "date_posted" not in df.columns:
        return df, 0, False

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_old)
    kept_rows: list[bool] = []
    seen_posted_value = False

    for value in df["date_posted"]:
        posted_dt = _coerce_posted_datetime(value)
        if posted_dt is None:
            kept_rows.append(True)
            continue
        seen_posted_value = True
        kept_rows.append(posted_dt >= cutoff)

    if not seen_posted_value:
        return df, 0, False

    filtered_df = df[kept_rows]
    removed = len(df) - len(filtered_df)
    return filtered_df, removed, True


# -- Location filtering ------------------------------------------------------


def _load_location_config(search_cfg: dict) -> tuple[list[str], list[str]]:
    """Extract accept/reject location lists from search config.

    Falls back to sensible defaults if not defined in the YAML.
    """
    accept = search_cfg.get("location_accept", [])
    reject = search_cfg.get("location_reject_non_remote", [])
    return accept, reject


def _location_ok(location: str | None, accept: list[str], reject: list[str]) -> bool:
    """Check if a job location passes the user's location filter.

    Remote jobs are always accepted. Non-remote jobs must match an accept
    pattern and not match a reject pattern.
    """
    if not location:
        return True  # unknown location -- keep it, let scorer decide

    loc = location.lower()

    # Remote jobs always OK
    if any(r in loc for r in ("remote", "anywhere", "work from home", "wfh", "distributed")):
        return True

    # Reject non-remote matches
    for r in reject:
        if r.lower() in loc:
            return False

    # Accept matches
    for a in accept:
        if a.lower() in loc:
            return True

    # No match -- reject unknown
    return False


def _resolve_jobspy_sites(sites: list[str], remote_only: bool) -> tuple[list[str], bool]:
    """Return non-Glassdoor sites after ApplyPilot's remote-only adjustments."""
    has_glassdoor = "glassdoor" in sites
    effective_sites = [site for site in sites if site != "glassdoor"]
    if remote_only and "indeed" in effective_sites:
        effective_sites = [site for site in effective_sites if site != "indeed"]
    return effective_sites, has_glassdoor


def _build_site_scrape_kwargs(
    *,
    site: str,
    search_term: str,
    location: str,
    results_per_site: int,
    hours_old: int,
    proxy_config: dict | None,
    remote_only: bool,
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
    if site == "indeed":
        kwargs["country_indeed"] = country_indeed
    if remote_only:
        kwargs["is_remote"] = True
    if proxy_config:
        kwargs["proxies"] = [proxy_config["jobspy"]]
    if site == "linkedin":
        kwargs["linkedin_fetch_description"] = True
    return kwargs


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
    country_indeed: str,
    max_retries: int,
    verbose: int = 0,
    hypothesis_id: str = "H1",
) -> tuple[list[object], list[dict]]:
    dataframes: list[object] = []
    failures: list[dict] = []

    for site in sites:
        kwargs = _build_site_scrape_kwargs(
            site=site,
            search_term=search_term,
            location=location,
            results_per_site=results_per_site,
            hours_old=hours_old,
            proxy_config=proxy_config,
            remote_only=remote_only,
            country_indeed=country_indeed,
            verbose=verbose,
        )
        started = time.time()
        try:
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

    return dataframes, failures


def _concat_site_results(dataframes: list[object]):
    if not dataframes:
        return pd.DataFrame()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        return pd.concat(dataframes, ignore_index=True) if len(dataframes) > 1 else dataframes[0]


# -- DB storage (JobSpy DataFrame -> SQLite) ---------------------------------


def store_jobspy_results(conn: sqlite3.Connection, df, source_label: str) -> tuple[int, int]:
    """Store JobSpy DataFrame results into the DB. Returns (new, existing)."""
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    existing = 0

    for _, row in df.iterrows():
        url = str(row.get("job_url", ""))
        if not url or url == "nan":
            continue

        title = _clean(row.get("title"))
        location_str = _clean(row.get("location"))

        salary = None
        min_amt = _clean(row.get("min_amount"))
        max_amt = _clean(row.get("max_amount"))
        interval = _clean(row.get("interval")) or ""
        currency = _clean(row.get("currency")) or ""
        if min_amt:
            if max_amt:
                salary = f"{currency}{int(float(min_amt)):,}-{currency}{int(float(max_amt)):,}"
            else:
                salary = f"{currency}{int(float(min_amt)):,}"
            if interval:
                salary += f"/{interval}"

        description = _clean(row.get("description"))
        site_name = str(row.get("site", source_label))
        is_remote = row.get("is_remote", False)

        site_label = f"{site_name}"
        if is_remote:
            location_str = f"{location_str} (Remote)" if location_str else "Remote"

        strategy = "jobspy"

        # If JobSpy gave us a full description, promote it directly
        full_description = None
        detail_scraped_at = None
        if description and len(description) > 200:
            full_description = description
            detail_scraped_at = now

        # Extract apply URL if JobSpy provided it
        apply_url = _clean(row.get("job_url_direct"))

        try:
            conn.execute(
                "INSERT INTO jobs (url, title, salary, description, location, site, strategy, discovered_at, "
                "full_description, application_url, detail_scraped_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    url,
                    title,
                    salary,
                    description,
                    location_str,
                    site_label,
                    strategy,
                    now,
                    full_description,
                    apply_url,
                    detail_scraped_at,
                ),
            )
            new += 1
        except sqlite3.IntegrityError:
            existing += 1

    commit_with_retry(conn)
    return new, existing


# -- Single search execution -------------------------------------------------


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
) -> dict:
    """Run a single search query and store results in DB."""
    s = search
    label = f'"{s["query"]}" in {s["location"]} {"(remote)" if s.get("remote") else ""}'
    if "tier" in s:
        label += f" [tier {s['tier']}]"

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
            "configured_sites": sites,
            "resolved_non_glassdoor_sites": other_sites,
            "glassdoor_enabled": has_glassdoor,
        },
    )

    all_dfs: list[object] = []

    # Run non-Glassdoor sites independently to preserve partial success when one board fails.
    if other_sites:
        site_dfs, non_gd_failures = _scrape_sites_independently(
            label=label,
            search_term=s["query"],
            location=s["location"],
            sites=other_sites,
            results_per_site=results_per_site,
            hours_old=hours_old,
            proxy_config=proxy_config,
            remote_only=bool(s.get("remote")),
            country_indeed=defaults.get("country_indeed", "usa"),
            max_retries=max_retries,
            verbose=0,
            hypothesis_id="H1",
        )
        all_dfs.extend(site_dfs)
        if non_gd_failures and all_dfs:
            log.warning(
                '[%s]: partial site success (%d/%d sites failed)',
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
        if s.get("remote"):
            gd_kwargs["is_remote"] = True
        if proxy_config:
            gd_kwargs["proxies"] = [proxy_config["jobspy"]]
        try:
            gd_df = _scrape_with_retry(gd_kwargs, max_retries=max_retries)
            all_dfs.append(gd_df)
        except Exception as e:
            log.error("[%s] (glassdoor): %s", label, e)

    if not all_dfs:
        log.error("[%s]: all sites failed", label)
        return {"new": 0, "existing": 0, "errors": 1, "filtered": 0, "total": 0, "label": label}

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
            ),
            axis=1,
        )
    ]
    filtered = before - len(df)

    conn = get_connection()
    new, existing = store_jobspy_results(conn, df, s["query"])

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
    }


# -- Single query search -----------------------------------------------------


def search_jobs(
    query: str,
    location: str,
    sites: list[str] | None = None,
    remote_only: bool = False,
    results_per_site: int = 50,
    hours_old: int = 72,
    proxy: str | None = None,
    country_indeed: str = "usa",
) -> dict:
    """Run a single job search via JobSpy and store results in DB."""
    if sites is None:
        sites = ["indeed", "linkedin", "zip_recruiter"]

    proxy_config = parse_proxy(proxy) if proxy else None
    effective_sites, has_glassdoor = _resolve_jobspy_sites(sites, remote_only)

    log.info('Search: "%s" in %s | sites=%s | remote=%s', query, location, sites, remote_only)
    _emit_debug_log(
        hypothesis_id="H5",
        location="jobspy.py:search_jobs",
        message="single-search invocation",
        data={
            "query": query,
            "location": location,
            "sites": sites,
            "remote_only": remote_only,
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
            "configured_sites": sites,
            "resolved_non_glassdoor_sites": effective_sites,
            "glassdoor_enabled": has_glassdoor,
        },
    )

    all_dfs: list[object] = []
    site_dfs, failures = _scrape_sites_independently(
        label=f'"{query}" in {location} {"(remote)" if remote_only else ""}',
        search_term=query,
        location=location,
        sites=effective_sites,
        results_per_site=results_per_site,
        hours_old=hours_old,
        proxy_config=proxy_config,
        remote_only=remote_only,
        country_indeed=country_indeed,
        max_retries=2,
        verbose=2,
        hypothesis_id="H5",
    )
    all_dfs.extend(site_dfs)

    if has_glassdoor:
        gd_kwargs = _build_site_scrape_kwargs(
            site="glassdoor",
            search_term=query,
            location=location,
            results_per_site=results_per_site,
            hours_old=hours_old,
            proxy_config=proxy_config,
            remote_only=remote_only,
            country_indeed=country_indeed,
            verbose=2,
        )
        try:
            all_dfs.append(_scrape_with_retry(gd_kwargs, max_retries=2))
        except Exception as e:
            failures.append({"site": "glassdoor", "exception_message": str(e)})
            log.error('["%s" in %s] (glassdoor): %s', query, location, e)

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

    conn = init_db()
    new, existing = store_jobspy_results(conn, df, query)
    log.info("Stored: %d new, %d already in DB", new, existing)

    db_total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE detail_scraped_at IS NULL").fetchone()[0]
    log.info("DB total: %d jobs, %d pending detail scrape", db_total, pending)

    return {"total": total, "new": new, "existing": existing}


# -- Full crawl (all queries x all locations) --------------------------------


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
    accept_locs, reject_locs = _load_location_config(search_cfg)

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
                    "tier": q.get("tier", 0),
                }
            )

    proxy_config = parse_proxy(proxy) if proxy else None

    log.info("Full crawl: %d search combinations", len(searches))
    log.info("Sites: %s | Results/site: %d | Hours old: %d", ", ".join(sites), results_per_site, hours_old)
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
    init_db()

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
        )
        completed += 1
        total_new += result["new"]
        total_existing += result["existing"]
        total_errors += result["errors"]

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
    conn = get_connection()
    db_total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

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


# -- Public entry point ------------------------------------------------------

def run_discovery(cfg: dict | None = None, sites_override: list[str] | None = None) -> dict:
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
