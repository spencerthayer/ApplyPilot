"""Site extraction pipeline: orchestrates fetch → plan → extract → store.

Single responsibility: runs the full extraction flow for one site URL.
All dependencies (fetcher, planner, extractors, DB) are injected.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from applypilot.discovery.smartextract.config import location_ok
from applypilot.discovery.smartextract.extractors import (
    ApiResponseExtractor,
    CssSelectorExtractor,
    JsonLdExtractor,
)
from applypilot.discovery.smartextract.fetcher import PlaywrightFetcher
from applypilot.discovery.smartextract.html_utils import (
    MIN_CONTENT_THRESHOLD,
    clean_page_html,
    detect_captcha,
)
from applypilot.discovery.smartextract.strategy import StrategyPlanner, judge_api_responses

log = logging.getLogger(__name__)


def _exception_summary(exc: Exception) -> str:
    """Return a minimal exception summary safe for logs."""
    return exc.__class__.__name__


def _store_jobs_filtered(
    jobs: list[dict],
    site: str,
    strategy: str,
    accept_locs: list[str],
    reject_locs: list[str],
        conn=None,
) -> tuple[int, int]:
    """Store jobs with location filtering. Returns (new, existing)."""
    from applypilot.bootstrap import get_app
    from applypilot.db.dto import JobDTO

    job_repo = get_app().container.job_repo
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    existing = 0
    filtered = 0

    for job in jobs:
        url = job.get("url")
        if not url:
            continue
        if not location_ok(job.get("location"), accept_locs, reject_locs):
            filtered += 1
            continue
        if job_repo.get_by_url(url):
            existing += 1
            continue
        job_repo.upsert(
            JobDTO(
                url=url,
                title=job.get("title"),
                salary=job.get("salary"),
                description=job.get("description"),
                location=job.get("location"),
                site=site,
                strategy=strategy,
                discovered_at=now,
            )
        )
        new += 1

    if filtered:
        log.info("Filtered %d jobs (wrong location)", filtered)
    return new, existing


class SiteExtractionPipeline:
    """Orchestrates the full extraction flow for one site URL.

    Dependencies are injected via constructor — no global imports of
    get_client() or init_db() inside the pipeline logic.
    """

    def __init__(self, llm_client) -> None:
        # Dependency injection: LLM client passed in, not fetched globally
        self._llm = llm_client
        self._planner = StrategyPlanner(llm_client)
        self._extractors = {
            "json_ld": JsonLdExtractor(),
            "api_response": ApiResponseExtractor(),
            "css_selectors": CssSelectorExtractor(llm_client),
        }

    def run_one_site(
        self,
        name: str,
        url: str,
        no_headful: bool = False,
        force_headful: bool = False,
    ) -> dict:
        """Run full smart extraction pipeline on one site URL."""
        log.info("=" * 60)
        log.info("%s: %s", name, url)

        # Step 1: Collect intelligence
        # force_headful skips headless entirely (for SPA sites like Naukri)
        use_headless = not force_headful
        log.debug("[smartextract] %s — force_headful: %s, starting headless: %s", name, force_headful, use_headless)

        log.info("[1] Collecting page intelligence...")
        fetcher = PlaywrightFetcher(headless=use_headless)
        try:
            intel = fetcher.fetch(url)
        except Exception as e:
            log.error("Page intelligence collection failed (%s)", _exception_summary(e))
            return {"name": name, "status": "ERROR", "error": str(e), "jobs": [], "total": 0, "titles": 0}

        log.info(
            "Done | JSON-LD: %d | API: %d | testids: %d | cards: %d",
            len(intel["json_ld"]),
            len(intel["api_responses"]),
            len(intel["data_testids"]),
            len(intel["card_candidates"]),
        )

        # Headful retry if page content is too small (and not force_headful, which already used headful)
        full_html = intel.get("full_html", "")
        cleaned_check = clean_page_html(full_html) if full_html else ""
        is_captcha = detect_captcha(full_html)
        log.debug("[smartextract] %s — CAPTCHA detected: %s, force_headful: %s", name, is_captcha, force_headful)

        if (
                not force_headful
                and len(cleaned_check) < MIN_CONTENT_THRESHOLD
                and full_html
                and not is_captcha
                and not no_headful
        ):
            log.info("Cleaned HTML only %s chars — retrying headful...", f"{len(cleaned_check):,}")
            try:
                fetcher_headful = PlaywrightFetcher(headless=False)
                intel = fetcher_headful.fetch(url)
            except Exception as e:
                log.warning("Headful retry failed (%s)", _exception_summary(e))
            log.info("Headful done | JSON-LD: %d | API: %d", len(intel["json_ld"]), len(intel["api_responses"]))
        elif is_captcha:
            log.warning("CAPTCHA/rate-limit detected — skipping headful retry")

        # Step 1.5: Judge filters API responses (LLM decides which are job-relevant)
        if intel["api_responses"]:
            log.info("[1.5] Judge filtering API responses...")
            intel["api_responses"] = judge_api_responses(intel["api_responses"], self._llm)
            log.info("Kept %d relevant responses", len(intel["api_responses"]))

        # Step 2: Strategy selection
        log.info("[2] Phase 1: Strategy selection...")
        try:
            plan = self._planner.plan(intel, site_name=name)
        except Exception as e:
            log.error("LLM_ERROR (%s)", _exception_summary(e))
            return {"name": name, "status": "LLM_ERROR", "error": str(e)}

        strategy = plan.get("strategy", "?")
        reasoning = plan.get("reasoning", "?")
        log.info("Strategy: %s | Reasoning: %s", strategy, reasoning)

        # Step 3: Execute extraction
        log.info("[3] Executing %s...", strategy)
        extractor = self._extractors.get(strategy)
        if not extractor:
            log.warning("Unknown strategy: %s", strategy)
            return {"name": name, "status": "UNKNOWN_STRATEGY", "plan": plan, "jobs": []}

        try:
            if strategy != "css_selectors":
                log.info("Extraction plan: %s", json.dumps(plan.get("extraction", {}))[:300])
            else:
                log.info("-> Phase 2: Generating selectors from card examples...")
            jobs = extractor.extract(intel, plan)
        except Exception as e:
            log.error("EXECUTION_ERROR (%s)", _exception_summary(e))
            return {"name": name, "status": "EXEC_ERROR", "error": str(e), "plan": plan}

        # Step 4: Report
        titles = sum(1 for j in jobs if j.get("title"))
        total = len(jobs)
        status = "PASS" if total > 0 and titles / max(total, 1) >= 0.8 else "FAIL" if total == 0 else "PARTIAL"

        urls = sum(1 for j in jobs if j.get("url"))
        salaries = sum(1 for j in jobs if j.get("salary"))
        descs = sum(1 for j in jobs if j.get("description"))
        log.info(
            "RESULT: %s — %d jobs, %d titles, %d urls, %d salaries, %d descriptions",
            status,
            total,
            titles,
            urls,
            salaries,
            descs,
        )
        log.debug("[smartextract] %s — stored: pending DB write", name)

        return {
            "name": name,
            "status": status,
            "strategy": strategy,
            "total": total,
            "titles": titles,
            "plan": plan,
            "jobs": jobs,
            "sample": jobs[:5],
        }


def run_all(
    targets: list[dict],
    accept_locs: list[str],
    reject_locs: list[str],
    llm_client,
        conn=None,
    workers: int = 1,
) -> dict:
    """Run smart extract on all targets.

    Targets are grouped by site name — each unique site runs in its own thread,
    processing all its queries sequentially. This prevents hammering the same
    domain from multiple threads simultaneously.

    workers defaults to os.cpu_count() when > 1 is requested.
    """
    import os
    from applypilot.bootstrap import get_app

    counts = get_app().container.job_repo.get_pipeline_counts()
    log.info(
        "Database: %d jobs already stored, %d pending detail scrape",
        counts["total"],
        counts["total"] - counts["with_desc"],
    )

    # Group targets by site name — each site gets one thread
    site_groups: dict[str, list[dict]] = {}
    for t in targets:
        site_groups.setdefault(t["name"], []).append(t)

    pipeline = SiteExtractionPipeline(llm_client)
    results: list[dict] = []
    total_new = 0
    total_existing = 0

    def _process_result(r: dict, target: dict) -> None:
        nonlocal total_new, total_existing
        jobs = r.get("jobs", [])
        if jobs:
            new, existing = _store_jobs_filtered(jobs, target["name"], r.get("strategy", "?"), accept_locs, reject_locs)
            total_new += new
            total_existing += existing
            log.info("DB: +%d new, %d already existed", new, existing)
            log.debug("[smartextract] %s — stored: %d new, %d existing", target["name"], new, existing)

    def _run_site_group(site_targets: list[dict]) -> list[dict]:
        """Run all queries for one site sequentially. Runs in its own thread."""
        site_results = []
        for i, target in enumerate(site_targets):
            label = target["name"]
            if target.get("query"):
                label = f"{target['name']} [{target['query']}]"
            log.info("[%s %d/%d] %s", target["name"], i + 1, len(site_targets), label)
            r = pipeline.run_one_site(
                target["name"],
                target["url"],
                target.get("no_headful", False),
                target.get("force_headful", False),
            )
            site_results.append((r, target))
        return site_results

    unique_sites = list(site_groups.values())

    if workers > 1 and len(unique_sites) > 1:
        # Use cpu_count as the thread ceiling — one thread per unique site
        max_workers = min(os.cpu_count() or 4, len(unique_sites))
        log.info("Parallel mode: %d unique sites, %d workers", len(unique_sites), max_workers)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_site = {pool.submit(_run_site_group, site_targets): site_targets for site_targets in unique_sites}
            for future in as_completed(future_to_site):
                for r, target in future.result():
                    results.append(r)
                    _process_result(r, target)
    else:
        for i, (name, site_targets) in enumerate(site_groups.items()):
            log.info("[site %d/%d] %s (%d queries)", i + 1, len(site_groups), name, len(site_targets))
            for r, target in _run_site_group(site_targets):
                results.append(r)
                _process_result(r, target)

    # Summary
    for r in results:
        strategy = r.get("strategy", "?")
        if r["status"] in ("PASS", "PARTIAL", "FAIL"):
            detail = f"{r['total']} jobs, {r['titles']} titles, strategy={strategy}"
        else:
            detail = r.get("error", "")[:60]
        log.info("%-10s | %-25s | %s", r["status"], r["name"], detail)

    passed = sum(1 for r in results if r["status"] == "PASS")
    log.info("%d/%d PASS", passed, len(results))

    return {"total_new": total_new, "total_existing": total_existing, "passed": passed, "total": len(results)}
