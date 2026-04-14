"""Page fetching: HTTP and Playwright backends.

Single responsibility: load a page and collect intelligence signals
(JSON-LD, API responses, data-testids, DOM stats, card candidates).
Implements the PageFetcher protocol for dependency injection.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Protocol, TypedDict, runtime_checkable

from playwright.sync_api import sync_playwright

log = logging.getLogger(__name__)

# Fix Windows encoding — prevents charmap errors on emoji/unicode in job titles
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _get_ua() -> str:
    """Build a realistic UA from the actual installed Chrome version."""
    from applypilot.apply.chrome import _get_real_user_agent

    return _get_real_user_agent()


# Module-level UA — computed once, reused across all fetches
UA = _get_ua()


class PageIntelligence(TypedDict, total=False):
    """Structured intelligence report from a page fetch."""

    url: str
    json_ld: list[dict]
    api_responses: list[dict]
    data_testids: list[dict]
    page_title: str
    dom_stats: dict
    card_candidates: list[dict]
    full_html: str
    next_data: dict


@runtime_checkable
class PageFetcher(Protocol):
    """Protocol for page fetching backends."""

    def fetch(self, url: str) -> PageIntelligence: ...


class PlaywrightFetcher:
    """Fetches pages using Playwright with API response interception.

    Collects JSON-LD, __NEXT_DATA__, data-testid attrs, DOM stats,
    repeating card candidates, and intercepted API responses.
    """

    def __init__(self, headless: bool = True) -> None:
        self._headless = headless

    def fetch(self, url: str) -> PageIntelligence:
        intel: PageIntelligence = {
            "url": url,
            "json_ld": [],
            "api_responses": [],
            "data_testids": [],
            "page_title": "",
            "dom_stats": {},
            "card_candidates": [],
        }

        captured_responses: list[dict] = []

        def on_response(response):
            ct = response.headers.get("content-type", "")
            rurl = response.url
            # Skip static assets — only capture potential API responses
            if any(ext in rurl for ext in [".js", ".css", ".png", ".jpg", ".svg", ".woff", ".ico", ".gif", ".webp"]):
                return
            if "json" in ct or "/api/" in rurl or "algolia" in rurl or "graphql" in rurl:
                try:
                    body = response.text()
                    try:
                        data = json.loads(body)
                    except Exception:
                        data = None
                    captured_responses.append(
                        {
                            "url": rurl,
                            "status": response.status,
                            "size": len(body),
                            "data": data,
                        }
                    )
                except Exception:
                    pass

        with sync_playwright() as p:
            from applypilot.enrichment.browser_config import STEALTH_INIT_SCRIPT as _STEALTH_INIT_SCRIPT

            browser = p.chromium.launch(headless=self._headless)
            context = browser.new_context(user_agent=UA)
            context.add_init_script(_STEALTH_INIT_SCRIPT)
            page = context.new_page()
            page.on("response", on_response)

            try:
                page.goto(url, timeout=60000)
                page.wait_for_load_state("networkidle", timeout=60000)
            except Exception:
                browser.close()
                raise

            intel["page_title"] = page.title()

            # 1. JSON-LD structured data
            for el in page.query_selector_all('script[type="application/ld+json"]'):
                try:
                    intel["json_ld"].append(json.loads(el.inner_text()))
                except Exception:
                    pass

            # 2. __NEXT_DATA__ (Next.js SSR payload)
            next_data = page.query_selector("script#__NEXT_DATA__")
            if next_data:
                try:
                    intel["next_data"] = json.loads(next_data.inner_text())
                except Exception:
                    pass

            # 3. data-testid attributes (useful for selector generation)
            intel["data_testids"] = page.evaluate("""
                () => {
                    const els = document.querySelectorAll('[data-testid]');
                    const results = [];
                    els.forEach(el => {
                        results.push({
                            testid: el.getAttribute('data-testid'),
                            tag: el.tagName.toLowerCase(),
                            text: el.innerText?.slice(0, 80) || ''
                        });
                    });
                    return results.slice(0, 50);
                }
            """)

            # 4. DOM statistics
            intel["dom_stats"] = page.evaluate("""
                () => {
                    const body = document.body;
                    return {
                        total_elements: body.querySelectorAll('*').length,
                        links: body.querySelectorAll('a[href]').length,
                        headings: body.querySelectorAll('h1,h2,h3,h4').length,
                        lists: body.querySelectorAll('ul,ol').length,
                        tables: body.querySelectorAll('table').length,
                        articles: body.querySelectorAll('article').length,
                        has_data_ids: body.querySelectorAll('[data-id]').length,
                    };
                }
            """)

            # 5. Repeating card-like elements (job listing candidates)
            intel["card_candidates"] = page.evaluate("""
                () => {
                    const candidates = [];
                    const allParents = document.querySelectorAll('*');
                    for (const parent of allParents) {
                        const children = Array.from(parent.children);
                        if (children.length < 3) continue;
                        const tagCounts = {};
                        children.forEach(c => { tagCounts[c.tagName] = (tagCounts[c.tagName] || 0) + 1; });
                        const dominant = Object.entries(tagCounts).sort((a,b) => b[1]-a[1])[0];
                        if (!dominant || dominant[1] < 3) continue;
                        const repeatingChildren = children.filter(c => c.tagName === dominant[0]);
                        const withText = repeatingChildren.filter(c => c.innerText?.trim().length > 20);
                        if (withText.length < 3) continue;
                        const withLinks = withText.filter(c => c.querySelector('a[href]'));
                        const score = withLinks.length * 2 + withText.length;
                        const parentId = parent.id ? '#' + parent.id : '';
                        const parentClasses = Array.from(parent.classList).filter(c => c.length < 30).slice(0, 3).join('.');
                        const parentTag = parent.tagName.toLowerCase();
                        const parentSelector = parentTag + (parentId || (parentClasses ? '.' + parentClasses : ''));
                        const childTag = dominant[0].toLowerCase();
                        const sampleChild = withText[0];
                        const childClasses = Array.from(sampleChild.classList).filter(c => c.length < 30).slice(0, 3).join('.');
                        const childSelector = childTag + (childClasses ? '.' + childClasses : '');
                        const examples = withText.slice(0, 3).map(c => {
                            const clone = c.cloneNode(true);
                            clone.querySelectorAll('script,style,svg,noscript').forEach(el => el.remove());
                            const html = clone.outerHTML;
                            return html.length > 5000 ? html.slice(0, 5000) + '...' : html;
                        });
                        candidates.push({
                            parent_selector: parentSelector, child_selector: childSelector,
                            child_tag: childTag, total_children: repeatingChildren.length,
                            with_text: withText.length, with_links: withLinks.length,
                            score: score, examples: examples,
                        });
                    }
                    candidates.sort((a,b) => b.score - a.score);
                    return candidates.slice(0, 3);
                }
            """)

            intel["full_html"] = page.content()
            browser.close()

        # Process captured API responses into structured summaries
        intel["api_responses"] = _summarize_api_responses(captured_responses)

        ld_count = len(intel["json_ld"])
        api_count = len(intel["api_responses"])
        card_count = len(intel["card_candidates"])
        method = "headless" if self._headless else "headful"
        log.debug("[smartextract] fetch method: %s, html: %d chars", method, len(intel.get("full_html", "")))
        log.debug("[smartextract] JSON-LD: %d, API responses: %d, cards: %d", ld_count, api_count, card_count)

        return intel


def _summarize_api_responses(captured: list[dict]) -> list[dict]:
    """Convert raw captured API responses into structured summaries for strategy selection."""
    summaries: list[dict] = []
    for resp in captured:
        summary: dict = {
            "url": resp["url"][:200],
            "status": resp["status"],
            "size": resp["size"],
            "_raw_data": resp.get("data"),
        }
        data = resp.get("data")
        if data:
            if isinstance(data, list) and data:
                summary["type"] = f"array[{len(data)}]"
                if isinstance(data[0], dict):
                    summary["first_item_keys"] = list(data[0].keys())[:20]
                    summary["first_item_sample"] = {k: str(v)[:100] for k, v in list(data[0].items())[:8]}
            elif isinstance(data, dict):
                summary["type"] = "object"
                summary["keys"] = list(data.keys())[:20]
                _explore_nested(data, "", summary)
        summaries.append(summary)
    return summaries


def _explore_nested(obj: dict, path_prefix: str, summary: dict, depth: int = 0) -> None:
    """Recursively explore nested API response structure for strategy briefing."""
    if depth > 3:
        return
    for key in list(obj.keys())[:15]:
        val = obj[key]
        path = f"{path_prefix}.{key}" if path_prefix else key
        if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
            info: dict = {
                "count": len(val),
                "first_item_keys": list(val[0].keys())[:20],
                "first_item_sample": {k: str(v)[:200] for k, v in list(val[0].items())[:8]},
            }
            for subkey in list(val[0].keys())[:10]:
                subval = val[0][subkey]
                if isinstance(subval, list) and len(subval) > 0 and isinstance(subval[0], dict):
                    info[f"first_item.{subkey}"] = {
                        "count": len(subval),
                        "first_item_keys": list(subval[0].keys())[:15],
                        "first_item_sample": {k: str(v)[:100] for k, v in list(subval[0].items())[:8]},
                    }
                elif isinstance(subval, dict):
                    info[f"first_item.{subkey}"] = {
                        "type": "object",
                        "keys": list(subval.keys())[:15],
                        "sample": {k: str(v)[:150] for k, v in list(subval.items())[:8]},
                    }
            summary[f"nested_{path}"] = info
        elif isinstance(val, dict) and depth < 3:
            _explore_nested(val, path, summary, depth + 1)
