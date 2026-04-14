"""Enrichment orchestrator — groups pending jobs by site and dispatches scraping.

Uses JobRepository for all DB access. No raw SQL.
"""

from __future__ import annotations

import logging
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

from applypilot.db.interfaces.job_repository import JobRepository
from applypilot.enrichment.scraper import scrape_site_batch

log = logging.getLogger(__name__)

SKIP_DETAIL_SITES = frozenset(
    {
        "Workday",
        "Greenhouse",
    }
)

SITE_DELAYS = {
    "RemoteOK": 3.0,
    "WelcomeToTheJungle": 2.0,
    "Job Bank Canada": 1.5,
    "CareerJet Canada": 3.0,
    "Hacker News Jobs": 1.0,
    "BuiltIn Remote": 2.0,
}

KNOWN_SITE_ORDER = [
    "RemoteOK",
    "Job Bank Canada",
    "BuiltIn Remote",
    "WelcomeToTheJungle",
    "CareerJet Canada",
    "Hacker News Jobs",
]


def _get_browser_config() -> tuple[str, str, dict | None]:
    """Return (stealth_init_script, user_agent, proxy_config)."""
    from applypilot.enrichment.browser_config import STEALTH_INIT_SCRIPT as _STEALTH_INIT_SCRIPT, UA, _PROXY_CONFIG

    return _STEALTH_INIT_SCRIPT, UA, _PROXY_CONFIG


def _build_site_order(site_jobs: dict[str, list]) -> list[str]:
    """Deterministic order with known sites first, rest shuffled."""
    order = [s for s in KNOWN_SITE_ORDER if s in site_jobs]
    rest = [s for s in sorted(site_jobs.keys()) if s not in order]
    random.shuffle(rest)
    return order + rest


def run_detail_scraper(
        job_repo: JobRepository,
        *,
        sites: list[str] | None = None,
        max_per_site: int | None = None,
        workers: int = 1,
        job_url: str | None = None,
) -> dict:
    """Groups pending jobs by site and processes each batch."""
    rows = job_repo.get_pending_enrichment(list(SKIP_DETAIL_SITES), job_url=job_url)

    if not rows:
        log.info("No pending jobs to scrape.")
        return {"processed": 0, "ok": 0, "partial": 0, "error": 0}

    site_jobs: dict[str, list[tuple]] = {}
    for row in rows:
        site = row.site
        if sites and site not in sites:
            continue
        site_jobs.setdefault(site, []).append((row.url, row.title))

    order = _build_site_order(site_jobs)
    log.info("Pending: %d jobs across %d sites (workers=%d)", len(rows), len(site_jobs), workers)

    stealth_script, ua, proxy_config = _get_browser_config()
    total_stats: dict = {"processed": 0, "ok": 0, "partial": 0, "error": 0, "tiers": {1: 0, 2: 0, 3: 0}}

    def _merge(stats: dict) -> None:
        for k in ("processed", "ok", "partial", "error"):
            total_stats[k] += stats[k]
        for t, count in stats.get("tiers", {}).items():
            total_stats["tiers"][t] = total_stats["tiers"].get(t, 0) + count

    if workers > 1 and len(order) > 1:

        def _scrape_site(site: str) -> dict:
            from applypilot.bootstrap import get_app

            thread_repo = get_app().container.job_repo
            jobs = site_jobs[site]
            delay = SITE_DELAYS.get(site, 2.0)
            return scrape_site_batch(
                thread_repo,
                site,
                jobs,
                delay=delay,
                max_jobs=max_per_site,
                stealth_init_script=stealth_script,
                ua=ua,
                proxy_config=proxy_config,
            )

        with ThreadPoolExecutor(max_workers=min(workers, len(order))) as pool:
            futures = {pool.submit(_scrape_site, site): site for site in order}
            for future in as_completed(futures):
                _merge(future.result())
    else:
        for site in order:
            jobs = site_jobs[site]
            delay = SITE_DELAYS.get(site, 2.0)
            log.info("%s -- %d jobs (delay=%.1fs)", site, len(jobs), delay)
            stats = scrape_site_batch(
                job_repo,
                site,
                jobs,
                delay=delay,
                max_jobs=max_per_site,
                stealth_init_script=stealth_script,
                ua=ua,
                proxy_config=proxy_config,
            )
            _merge(stats)
            log.info(
                "Site summary: %d ok, %d partial, %d error | T1=%d T2=%d T3=%d",
                stats["ok"],
                stats["partial"],
                stats["error"],
                stats["tiers"].get(1, 0),
                stats["tiers"].get(2, 0),
                stats["tiers"].get(3, 0),
            )

    log.info(
        "TOTAL: %d processed | %d ok | %d partial | %d error",
        total_stats["processed"],
        total_stats["ok"],
        total_stats["partial"],
        total_stats["error"],
    )
    llm_calls = total_stats["tiers"].get(3, 0)
    total = total_stats["processed"]
    if total > 0:
        log.info("LLM calls: %d/%d (%.0f%% saved)", llm_calls, total, ((total - llm_calls) / total) * 100)

    return total_stats


def run_enrichment(
        job_repo: JobRepository,
        *,
        limit: int = 100,
        workers: int = 1,
        job_url: str | None = None,
) -> dict:
    """Main entry point for detail page enrichment.

    When called from a worker thread (chunked mode), the passed job_repo
    may hold a main-thread connection. Get a fresh one from the container.
    """
    from applypilot.bootstrap import get_app

    job_repo = get_app().container.job_repo  # thread-local via get_connection()

    from applypilot.enrichment.url_resolver import resolve_all_urls, resolve_wttj_urls

    url_stats = resolve_all_urls(job_repo)
    log.info(
        "URL resolution: %d resolved, %d absolute, %d failed",
        url_stats["resolved"],
        url_stats["already_absolute"],
        url_stats["failed"],
    )

    # WTTJ special handling
    wttj_count = job_repo.get_wttj_count()
    if wttj_count > 0:
        sample_url = job_repo.get_wttj_sample_url()
        if sample_url and not sample_url.startswith("http"):
            stealth_script, ua, _ = _get_browser_config()
            updated = resolve_wttj_urls(job_repo, stealth_script, ua)
            log.info("WTTJ: %d URLs updated", updated)

    return run_detail_scraper(job_repo, max_per_site=limit, workers=workers, job_url=job_url)
