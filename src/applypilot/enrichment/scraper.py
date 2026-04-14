"""Detail page scraper — runs the 3-tier cascade on individual pages.

Uses JobRepository for all DB writes. No raw SQL.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from applypilot.db.dto import EnrichErrorDTO, EnrichResultDTO
from applypilot.db.interfaces.job_repository import JobRepository
from applypilot.enrichment.cascade.css_selector import (
    extract_apply_url_deterministic,
    extract_description_deterministic,
)
from applypilot.enrichment.cascade.html_utils import collect_detail_intelligence
from applypilot.enrichment.cascade.jsonld import extract_from_json_ld
from applypilot.enrichment.cascade.llm_extractor import extract_with_llm

log = logging.getLogger(__name__)

PERMANENT_FAILURES = {404, 410, 451}
MAX_DETAIL_RETRIES = 5

_RETRIABLE_PATTERNS = (
    "timeout",
    "LLM error",
    "Client error",
    "HTTP 408",
    "HTTP 429",
    "HTTP 500",
    "HTTP 502",
    "HTTP 503",
    "HTTP 504",
    "ERR_CONNECTION",
    "ERR_NAME_NOT_RESOLVED",
    "ERR_INTERNET_DISCONNECTED",
    "net::ERR",
)
_EXPIRED_PATTERNS = ("HTTP 404", "HTTP 410", "HTTP 451")
_PERMANENT_PATTERNS = ("no data extracted", "manual://")
_LOGIN_PAGE_TITLES = ("login", "sign in", "log in", "create account", "register")


def classify_detail_error(error: str, current_retry_count: int) -> tuple[str, str | None]:
    """Classify an enrichment error and compute the next retry timestamp.

    Returns (category, next_retry_at_iso).
    """
    if current_retry_count >= MAX_DETAIL_RETRIES:
        return "permanent", None
    if any(p in error for p in _EXPIRED_PATTERNS):
        return "expired", None
    if any(p in error for p in _PERMANENT_PATTERNS):
        return "permanent", None
    if any(p in error for p in _RETRIABLE_PATTERNS):
        delay_minutes = min(5 * (4 ** current_retry_count), 24 * 60)
        next_retry = datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
        return "retriable", next_retry.isoformat()
    return "permanent", None


def scrape_detail_page(page, url: str) -> dict:
    """Full 3-tier cascade for one detail page. Pure — no DB access."""
    result: dict = {
        "full_description": None,
        "application_url": None,
        "status": "error",
        "tier_used": None,
        "error": None,
    }
    t0 = time.time()

    try:
        resp = page.goto(url, timeout=45000)
        if resp and resp.status in PERMANENT_FAILURES:
            result["error"] = f"HTTP {resp.status}"
            result["elapsed"] = time.time() - t0
            return result
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        # SPA content-ready wait: many job sites (Amazon, Workday, Greenhouse)
        # render JD via React/Angular after initial page load. Wait for a
        # content indicator before extracting.
        _JD_SELECTORS = [
            "[class*='description']",
            "[class*='job-description']",
            "[id*='description']",
            "[data-automation*='description']",
            "h2:has-text('Description')",
            "h2:has-text('Qualifications')",
            "h2:has-text('Requirements')",
            "h2:has-text('Responsibilities')",
            ".posting-requirements",
            ".job-details",
        ]
        for sel in _JD_SELECTORS:
            try:
                page.wait_for_selector(sel, timeout=5000)
                break
            except Exception:
                continue
    except Exception as e:
        err_str = str(e)
        result["error"] = "timeout" if "timeout" in err_str.lower() else err_str[:200]
        result["elapsed"] = time.time() - t0
        return result

    intel = collect_detail_intelligence(page)

    # Tier 1: JSON-LD
    json_ld_result = extract_from_json_ld(intel)
    if json_ld_result and json_ld_result.get("full_description"):
        result.update(json_ld_result)
        result["tier_used"] = 1
        if not result.get("application_url"):
            apply = extract_apply_url_deterministic(page)
            if apply:
                result["application_url"] = apply
        result["status"] = "ok" if result.get("application_url") else "partial"
        result["elapsed"] = time.time() - t0
        return result

    # Tier 2: Deterministic CSS
    desc = extract_description_deterministic(page)
    apply = extract_apply_url_deterministic(page)
    if desc:
        result["full_description"] = desc
        result["application_url"] = apply
        result["tier_used"] = 2
        result["status"] = "ok" if apply else "partial"
        result["elapsed"] = time.time() - t0
        return result

    tier2_apply = apply

    # Tier 3: LLM
    llm_result = extract_with_llm(page, url)
    result["full_description"] = llm_result.get("full_description")
    result["application_url"] = llm_result.get("application_url") or tier2_apply
    result["tier_used"] = 3

    if result.get("full_description"):
        result["status"] = "ok" if result.get("application_url") else "partial"
    elif result.get("application_url"):
        result["status"] = "partial"
    else:
        result["status"] = "error"
        result["error"] = "no data extracted"

    result["elapsed"] = time.time() - t0
    return result


def scrape_site_batch(
        job_repo: JobRepository,
        site: str,
        jobs: list[tuple],
        *,
        delay: float = 2.0,
        max_jobs: int | None = None,
        stealth_init_script: str = "",
        ua: str = "",
        proxy_config: dict | None = None,
) -> dict:
    """Process all jobs for one site using shared browser context.

    All DB writes go through job_repo.
    """
    from playwright.sync_api import sync_playwright

    stats: dict = {"processed": 0, "ok": 0, "partial": 0, "error": 0, "tiers": {1: 0, 2: 0, 3: 0}}

    if max_jobs:
        jobs = jobs[:max_jobs]
    if not jobs:
        return stats

    now = datetime.now(timezone.utc).isoformat()

    with sync_playwright() as p:
        launch_opts: dict = {"headless": True}
        if proxy_config:
            launch_opts["proxy"] = proxy_config.get("playwright")
        browser = p.chromium.launch(**launch_opts)
        context = browser.new_context(user_agent=ua)
        if stealth_init_script:
            context.add_init_script(stealth_init_script)
        page = context.new_page()

        for i, (url, title) in enumerate(jobs):
            log.info("[%d/%d] %s", i + 1, len(jobs), (title or url)[:50])

            result = scrape_detail_page(page, url)
            stats["processed"] += 1

            # Detect login/auth walls from page title
            page_title = (result.get("page_title") or title or "").lower()
            if any(p in page_title for p in _LOGIN_PAGE_TITLES) and not result.get("full_description"):
                result["status"] = "error"
                result["error"] = f"login_required:{page_title[:50]}"

            # LinkedIn-specific: no JD extracted = auth wall (public page shows poster name, not JD)
            if "linkedin.com" in url and not result.get("full_description"):
                result["status"] = "error"
                result["error"] = "login_required:linkedin_auth_wall"

            tier = result.get("tier_used")
            status = result["status"]
            elapsed = result.get("elapsed", 0)

            if tier:
                stats["tiers"][tier] = stats["tiers"].get(tier, 0) + 1

            tier_str = f"T{tier}" if tier else "--"
            desc_len = len(result.get("full_description") or "")
            apply_str = "yes" if result.get("application_url") else "no"
            err_str = f" | err={result.get('error')}" if result.get("error") else ""

            log.info(
                "  %s | %s | desc=%s chars | apply=%s | %.1fs%s",
                status,
                tier_str,
                f"{desc_len:,}",
                apply_str,
                elapsed,
                err_str,
            )

            if status in ("ok", "partial"):
                stats[status] += 1
                job_repo.update_enrichment(
                    EnrichResultDTO(
                        url=url,
                        full_description=result.get("full_description"),
                        application_url=result.get("application_url"),
                        detail_scraped_at=now,
                    )
                )
                try:
                    from applypilot.analytics.helpers import emit_job_enriched

                    emit_job_enriched(url, site, tier or 0, desc_len, elapsed, status)
                except Exception:
                    pass
            else:
                stats["error"] += 1
                error_msg = result.get("error", "unknown")
                retry_count = job_repo.get_detail_retry_count(url)
                category, next_retry_at = classify_detail_error(error_msg, retry_count)
                job_repo.update_enrichment_error(
                    EnrichErrorDTO(
                        url=url,
                        detail_error=error_msg,
                        detail_error_category=category,
                        detail_retry_count=retry_count + 1,
                        detail_next_retry_at=next_retry_at,
                        detail_scraped_at=now,
                    )
                )
                try:
                    from applypilot.analytics.helpers import emit_job_enriched

                    emit_job_enriched(url, site, tier or 0, 0, elapsed, "error")
                except Exception:
                    pass
                log.info(
                    "  error_category=%s retry=%d/%d next=%s",
                    category,
                    retry_count + 1,
                    MAX_DETAIL_RETRIES,
                    next_retry_at or "never",
                )

            if i < len(jobs) - 1:
                time.sleep(delay)

        browser.close()

    return stats
