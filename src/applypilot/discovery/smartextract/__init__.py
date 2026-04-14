"""SmartExtract: AI-powered job discovery from arbitrary websites.

Public API — maintains backward compatibility with all external callers:
  - pipeline/stages.py:     from applypilot.discovery.smartextract import run_smart_extract
  - resume_ingest.py:       from applypilot.discovery.smartextract import extract_json
  - enrichment/detail.py:   from applypilot.discovery.smartextract import extract_json
  - tests/test_security_logging.py: from applypilot.discovery import smartextract
"""

import logging

from applypilot import config as _app_config
from applypilot.llm import get_client

# Re-export shared utilities for backward compat
from applypilot.discovery.smartextract.json_utils import extract_json, resolve_json_path  # noqa: F401
from applypilot.discovery.smartextract.config import build_scrape_targets, load_sites  # noqa: F401
from applypilot.discovery.smartextract.pipeline import run_all

# Re-export internals that tests monkeypatch (backward compat for test_security_logging.py)
from applypilot.discovery.smartextract.fetcher import PlaywrightFetcher, PageIntelligence  # noqa: F401
from applypilot.discovery.smartextract.strategy import format_strategy_briefing  # noqa: F401
from applypilot.discovery.smartextract.extractors import JsonLdExtractor  # noqa: F401

log = logging.getLogger(__name__)


# -- Backward-compat shims for test_security_logging.py monkeypatching --
# These module-level functions mirror the old monolith API so tests that
# monkeypatch smartextract.collect_page_intelligence etc. still work.


def collect_page_intelligence(url: str, headless: bool = True) -> PageIntelligence:
    """Backward-compat wrapper around PlaywrightFetcher.fetch()."""
    return PlaywrightFetcher(headless=headless).fetch(url)


def ask_llm(prompt: str) -> tuple[str, float, dict]:
    """Backward-compat wrapper: send prompt to LLM."""
    import time

    client = get_client()
    t0 = time.time()
    text = client.chat([{"role": "user", "content": prompt}], max_output_tokens=4096)
    elapsed = time.time() - t0
    return text, elapsed, {"finish_reason": "stop", "prompt_chars": len(prompt), "response_chars": len(text)}


def execute_json_ld(intel: dict, plan: dict) -> list[dict]:
    """Backward-compat wrapper around JsonLdExtractor."""
    return JsonLdExtractor().extract(intel, plan)


def _run_one_site(name: str, url: str, no_headful: bool = False) -> dict:
    """Backward-compat wrapper for per-site extraction.

    Uses module-level shims (collect_page_intelligence, ask_llm, etc.)
    so that monkeypatching in tests works correctly.
    """
    from applypilot.discovery.smartextract.html_utils import clean_page_html, detect_captcha, MIN_CONTENT_THRESHOLD

    log.info("=" * 60)
    log.info("%s: %s", name, url)

    log.info("[1] Collecting page intelligence...")
    try:
        intel = collect_page_intelligence(url)
    except Exception as e:
        log.error("Page intelligence collection failed (%s)", e.__class__.__name__)
        return {"name": name, "status": "ERROR", "error": str(e), "jobs": [], "total": 0, "titles": 0}

    log.info(
        "Done | JSON-LD: %d | API: %d | testids: %d | cards: %d",
        len(intel["json_ld"]),
        len(intel["api_responses"]),
        len(intel["data_testids"]),
        len(intel["card_candidates"]),
    )

    # Headful retry
    full_html = intel.get("full_html", "")
    cleaned_check = clean_page_html(full_html) if full_html else ""
    is_captcha = detect_captcha(full_html)
    if len(cleaned_check) < MIN_CONTENT_THRESHOLD and full_html and not is_captcha and not no_headful:
        log.info("Cleaned HTML only %s chars — retrying headful...", f"{len(cleaned_check):,}")
        try:
            intel = collect_page_intelligence(url, headless=False)
        except Exception:
            pass
    elif is_captcha:
        log.warning("CAPTCHA/rate-limit detected — skipping headful retry")

    # Strategy selection
    briefing = format_strategy_briefing(intel)
    log.info("[2] Phase 1: Strategy selection (%s chars briefing)", f"{len(briefing):,}")

    try:
        raw, elapsed, meta = ask_llm("strategy prompt placeholder")
    except Exception as e:
        log.error("LLM_ERROR (%s)", e.__class__.__name__)
        return {"name": name, "status": "LLM_ERROR", "error": str(e)}

    try:
        plan = extract_json(raw)
    except Exception as e:
        log.error("PARSE_ERROR (%s)", e.__class__.__name__)
        return {"name": name, "status": "PARSE_ERROR", "error": str(e), "raw": raw}

    strategy = plan.get("strategy", "?")
    log.info("Strategy: %s", strategy)

    # Execute
    log.info("[3] Executing %s...", strategy)
    try:
        if strategy == "json_ld":
            jobs = execute_json_ld(intel, plan)
        elif strategy == "api_response":
            from applypilot.discovery.smartextract.extractors import ApiResponseExtractor

            jobs = ApiResponseExtractor().extract(intel, plan)
        elif strategy == "css_selectors":
            from applypilot.discovery.smartextract.extractors import CssSelectorExtractor

            jobs = CssSelectorExtractor(get_client()).extract(intel, plan)
        else:
            jobs = []
    except Exception as e:
        log.error("EXECUTION_ERROR (%s)", e.__class__.__name__)
        return {"name": name, "status": "EXEC_ERROR", "error": str(e), "plan": plan}

    titles = sum(1 for j in jobs if j.get("title"))
    total = len(jobs)
    status = "PASS" if total > 0 and titles / max(total, 1) >= 0.8 else "FAIL" if total == 0 else "PARTIAL"
    log.info("RESULT: %s — %d jobs, %d titles", status, total, titles)

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


def run_smart_extract(
    sites: list[dict] | None = None,
    workers: int = 0,
        employer_keys: list[str] | None = None,
) -> dict:
    """Main entry point for AI-powered smart extraction.

    Loads sites from config/sites.yaml and search queries from the user's
    search config, then runs the extraction pipeline on all targets.

    Args:
        sites: Override the site list. If None, loads from YAML.
        workers: Parallel threads. 0 = auto (CPU count). Safe because
                 run_all groups targets by site — each thread handles one
                 unique domain, running its queries sequentially.

    Returns:
        Dict with stats: total_new, total_existing, passed, total.
    """
    import os

    # SmartExtract is safe to parallelize — run_all groups by site, so each
    # thread handles one unique domain sequentially. Use at least CPU count,
    # but respect caller if they explicitly request more.
    cpu_count = os.cpu_count() or 4
    workers = max(workers, cpu_count)
    search_cfg = _app_config.load_search_config()
    from applypilot.discovery.smartextract.config import load_location_filter

    accept_locs, reject_locs = load_location_filter(search_cfg)

    targets = build_scrape_targets(sites=sites, search_cfg=search_cfg)

    if not targets:
        log.warning("No scrape targets configured. Create config/sites.yaml and searches.yaml.")
        return {"total_new": 0, "total_existing": 0, "passed": 0, "total": 0}

    # Filter targets by registry-mapped site names
    if employer_keys is not None:
        targets = [t for t in targets if t["name"] in employer_keys]
        if not targets:
            log.info("No SmartExtract sites match requested companies")
            return {"total_new": 0, "total_existing": 0, "passed": 0, "total": 0}
        log.info("SmartExtract: filtered to %d targets by --company", len(targets))

    effective_sites = sites or load_sites()
    search_sites = sum(1 for s in effective_sites if s.get("type") == "search")
    static_sites = sum(1 for s in effective_sites if s.get("type") != "search")
    log.info(
        "Sites: %d searchable, %d static | Total targets: %d (workers=%d)",
        search_sites,
        static_sites,
        len(targets),
        workers,
    )

    # Inject dependencies: LLM client
    llm_client = get_client()

    return run_all(targets, accept_locs, reject_locs, llm_client, workers=workers)
