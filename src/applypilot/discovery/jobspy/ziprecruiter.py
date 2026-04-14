"""Ziprecruiter."""

from applypilot import config

__all__ = [
    "_JOBSPY_PARAMS",
    "_ziprecruiter_search_url",
    "_merge_ziprecruiter_page_data",
    "_classify_ziprecruiter_page",
    "_inspect_ziprecruiter_page",
    "_scrape_ziprecruiter_browser",
]

"""JobSpy-based job discovery: searches Indeed, LinkedIn, Glassdoor, ZipRecruiter.

Uses python-jobspy to scrape multiple job boards, deduplicates results,
parses salary ranges, and stores everything in the ApplyPilot database.

Search queries, locations, and filtering rules are loaded from the user's
search configuration YAML (searches.yaml) rather than being hardcoded.
"""

import inspect as _inspect
from urllib.parse import urlencode

import pandas as pd
from jobspy import scrape_jobs as _raw_scrape_jobs

# Only pass params that the installed jobspy version actually accepts.
_JOBSPY_PARAMS = set(_inspect.signature(_raw_scrape_jobs).parameters.keys())

from applypilot.discovery.jobspy.debug import _emit_debug_log


def _ziprecruiter_search_url(
        search_term: str,
        location: str,
        remote_only: bool,
        page_number: int,
        distance: int | None = None,
) -> str:
    params = {
        "search": search_term,
        "location": location,
        "form": "jobs-landing",
    }
    if remote_only:
        params["refine_by_location_type"] = "only_remote"
    elif distance is not None:
        params["radius"] = distance
    base = "https://www.ziprecruiter.com/jobs-search"
    if page_number > 1:
        base += f"/{page_number}"
    return f"{base}?{urlencode(params)}"


def _merge_ziprecruiter_page_data(item_list: list[dict], cards: list[dict]) -> list[dict]:
    merged: list[dict] = []
    row_count = max(len(item_list), len(cards))
    for idx in range(row_count):
        item = item_list[idx] if idx < len(item_list) else {}
        card = cards[idx] if idx < len(cards) else {}
        job_url = item.get("url") or card.get("url") or ""
        title = item.get("name") or card.get("title") or ""
        if not job_url and not title:
            continue
        location = card.get("location") or ""
        merged.append(
            {
                "job_url": job_url,
                "title": title,
                "company": card.get("company") or "",
                "location": location,
                "salary": card.get("salary") or None,
                "description": None,
                "date_posted": None,
                "site": "zip_recruiter",
                "is_remote": "remote" in location.lower(),
            }
        )
    return merged


def _classify_ziprecruiter_page(payload: dict) -> dict[str, object]:
    item_list = payload.get("itemList") or []
    cards = payload.get("cards") or []
    markers = payload.get("markers") or {}
    if item_list or cards:
        state = "results"
    elif markers.get("challenge_or_block"):
        state = "challenge_or_block"
    elif markers.get("empty"):
        state = "empty"
    else:
        state = "unexpected_layout"
    return {
        "state": state,
        "itemList": item_list,
        "cards": cards,
        "item_list_count": len(item_list),
        "card_count": len(cards),
        "text_sample": str(payload.get("textSample") or "")[:240],
    }


def _inspect_ziprecruiter_page(page) -> dict[str, object]:
    payload = page.evaluate(
        """
        () => {
            const itemList = [];
            for (const script of Array.from(document.querySelectorAll('script[type="application/ld+json"]'))) {
                try {
                    const parsed = JSON.parse(script.textContent || 'null');
                    if (parsed && parsed['@type'] === 'ItemList' && Array.isArray(parsed.itemListElement)) {
                        for (const item of parsed.itemListElement) {
                            itemList.push({
                                name: item?.name || '',
                                url: item?.url || '',
                            });
                        }
                        break;
                    }
                } catch (_) {}
            }

            const seen = new Set();
            const cards = [];
            for (const article of Array.from(document.querySelectorAll('article[id^="job-card-"]'))) {
                const id = article.id || '';
                if (!id || seen.has(id)) continue;
                const title = article.querySelector('h2')?.textContent?.trim() || '';
                if (!title) continue;
                seen.add(id);
                const paragraphs = Array.from(article.querySelectorAll('p'))
                    .map((p) => (p.textContent || '').trim())
                    .filter(Boolean);
                const hrefs = Array.from(article.querySelectorAll('a[href]'))
                    .map((a) => a.getAttribute('href') || '')
                    .filter(Boolean);
                const preferredHref = hrefs.find((href) => {
                    const lowered = href.toLowerCase();
                    return /\\/jobs?\\//i.test(href) || /\\/c\\/.+\\/job\\//i.test(lowered) || lowered.includes('jid=');
                }) || hrefs.find((href) => !href.startsWith('/co/') && !href.startsWith('#')) || '';
                let jobUrl = '';
                if (preferredHref) {
                    try {
                        jobUrl = new URL(preferredHref, window.location.origin).href;
                    } catch (_) {
                        jobUrl = preferredHref;
                    }
                }
                cards.push({
                    id,
                    url: jobUrl,
                    title,
                    company: article.querySelector('a[href^="/co/"]')?.textContent?.trim() || '',
                    location: paragraphs.find((text) => text.includes('·')) || '',
                    salary: paragraphs.find((text) => /\\$|\\/hr|\\/yr/.test(text)) || '',
                });
            }

            const textSample = [document.title || '', document.body?.innerText || '']
                .filter(Boolean)
                .join('\\n')
                .slice(0, 4000);
            const loweredText = textSample.toLowerCase();
            const challengeSignals = [
                'cloudflare',
                'verify you are human',
                'verify that you are human',
                'access denied',
                'forbidden',
                'security check',
                'unusual traffic',
                'complete the security check',
                'checking your browser',
                'challenge',
            ];
            const emptySignals = [
                'no jobs found',
                'no matching jobs',
                'no matching job',
                'no results found',
                '0 jobs',
                'try another search',
            ];

            return {
                itemList,
                cards,
                markers: {
                    challenge_or_block: challengeSignals.some((token) => loweredText.includes(token)),
                    empty: emptySignals.some((token) => loweredText.includes(token))
                        || Boolean(
                            document.querySelector(
                                '[data-testid*="no-results"], [class*="no-results"], [id*="no-results"]'
                            )
                        ),
                },
                textSample,
            };
        }
        """
    )
    return _classify_ziprecruiter_page(payload)


def _scrape_ziprecruiter_browser(
        *,
        search_term: str,
        location: str,
        results_wanted: int,
        remote_only: bool,
        distance: int | None = None,
        proxy_config: dict | None = None,
):
    """Scrape ZipRecruiter with a real browser to clear Cloudflare and parse rendered results."""
    from playwright.sync_api import sync_playwright

    from applypilot.apply.chrome import _get_real_user_agent
    from applypilot.enrichment.browser_config import STEALTH_INIT_SCRIPT as _STEALTH_INIT_SCRIPT

    launch_opts: dict = {"headless": True}
    try:
        chrome_path = config.get_chrome_path()
    except FileNotFoundError:
        chrome_path = None
    if chrome_path:
        launch_opts["executable_path"] = chrome_path
    if proxy_config:
        launch_opts["proxy"] = proxy_config["playwright"]

    per_page = 20
    page_count = max(1, min(5, (results_wanted + per_page - 1) // per_page))
    collected: list[dict] = []
    seen_urls: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_opts)
        try:
            context = browser.new_context(user_agent=_get_real_user_agent())
            context.add_init_script(_STEALTH_INIT_SCRIPT)
            page = context.new_page()

            for page_number in range(1, page_count + 1):
                page.goto(
                    _ziprecruiter_search_url(
                        search_term,
                        location,
                        remote_only,
                        page_number,
                        distance,
                    ),
                    timeout=60000,
                    wait_until="domcontentloaded",
                )
                selector_ready = True
                try:
                    page.wait_for_selector(
                        "article[id^='job-card-'], script[type='application/ld+json']",
                        state="attached",
                        timeout=30000,
                    )
                except Exception:
                    selector_ready = False
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass

                page_info = _inspect_ziprecruiter_page(page)
                _emit_debug_log(
                    hypothesis_id="H4",
                    location="jobspy.py:_scrape_ziprecruiter_browser",
                    message="ziprecruiter page classified",
                    data={
                        "query": search_term,
                        "page_number": page_number,
                        "selector_ready": selector_ready,
                        "state": page_info["state"],
                        "item_list_count": page_info["item_list_count"],
                        "card_count": page_info["card_count"],
                        "text_sample": page_info["text_sample"],
                    },
                )
                if page_info["state"] == "challenge_or_block":
                    break
                if page_info["state"] == "empty":
                    break
                if page_info["state"] == "unexpected_layout":
                    raise RuntimeError(
                        "ziprecruiter unexpected_layout"
                        + (f": {page_info['text_sample']}" if page_info["text_sample"] else "")
                    )

                merged = _merge_ziprecruiter_page_data(page_info["itemList"], page_info["cards"])
                page_new = 0
                for row in merged:
                    url = row.get("job_url") or ""
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    collected.append(row)
                    page_new += 1
                    if len(collected) >= results_wanted:
                        break

                if page_new == 0 or len(collected) >= results_wanted:
                    break
        finally:
            browser.close()

    return pd.DataFrame(collected)
