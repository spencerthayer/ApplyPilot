"""Extraction implementations: JSON-LD, API response, CSS selectors.

Each extractor implements the same protocol: extract(intel, plan) -> list[dict].
New extractors (e.g. __NEXT_DATA__) can be added without modifying existing code.
"""

from __future__ import annotations

import logging
import time
from typing import Protocol, runtime_checkable

from bs4 import BeautifulSoup

from applypilot.discovery.smartextract.html_utils import clean_page_html
from applypilot.discovery.smartextract.json_utils import extract_json, resolve_json_path, resolve_json_path_raw

log = logging.getLogger(__name__)


@runtime_checkable
class Extractor(Protocol):
    """Protocol for job data extractors."""

    def extract(self, intel: dict, plan: dict) -> list[dict]: ...


class JsonLdExtractor:
    """Extract jobs from JSON-LD JobPosting entries — zero LLM calls."""

    def extract(self, intel: dict, plan: dict) -> list[dict]:
        ext = plan.get("extraction", {})
        jobs: list[dict] = []
        for entry in intel.get("json_ld", []):
            if not isinstance(entry, dict) or entry.get("@type") != "JobPosting":
                continue
            job: dict = {}
            for field in ("title", "salary", "description", "location", "url"):
                path = ext.get(field)
                if not path or path == "null":
                    job[field] = None
                    continue
                job[field] = resolve_json_path(entry, path)
            jobs.append(job)
        log.debug("[smartextract] extractor: json_ld, jobs found: %d", len(jobs))
        if jobs:
            log.debug("[smartextract] sample: %s @ %s", (jobs[0].get("title") or "?")[:40], jobs[0].get("company", "?"))
        return jobs


class ApiResponseExtractor:
    """Extract jobs from intercepted API response data — zero LLM calls."""

    def extract(self, intel: dict, plan: dict) -> list[dict]:
        ext = plan.get("extraction", {})
        url_pattern = ext.get("url_pattern", "")

        target_data = None
        for resp in intel.get("api_responses", []):
            if url_pattern in resp.get("url", ""):
                target_data = resp.get("_raw_data")
                break

        if not target_data:
            log.warning("Could not find stored API response matching: %s", url_pattern)
            return []

        items_path = ext.get("items_path", "")
        items = resolve_json_path_raw(target_data, items_path)
        if not isinstance(items, list):
            log.warning("items_path '%s' did not resolve to a list (got %s)", items_path, type(items).__name__)
            return []

        jobs: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            job: dict = {}
            for field in ("title", "salary", "description", "location", "url"):
                path = ext.get(field)
                if not path or path == "null":
                    job[field] = None
                    continue
                job[field] = resolve_json_path(item, path)
            jobs.append(job)

        log.debug("[smartextract] extractor: api_response, jobs found: %d", len(jobs))
        if jobs:
            log.debug("[smartextract] sample: %s @ %s", (jobs[0].get("title") or "?")[:40], jobs[0].get("company", "?"))
        return jobs


# -- CSS selector prompt --

_FULL_PAGE_SELECTOR_PROMPT = """You are a senior web scraping engineer. Below is the cleaned HTML of a job listings page.

Your task:
1. Find the repeating HTML elements that represent individual job listings
2. Generate CSS selectors to extract data from them

Return a JSON object:
- "job_card": CSS selector matching each job card (MUST match ALL cards on the page)
- "title": selector RELATIVE to the card for the job title
- "salary": selector relative to card for salary, or null
- "description": selector relative to card for description snippet, or null
- "location": selector relative to card for location, or null
- "url": selector relative to card for the link (<a> tag) to the job detail page

Selector rules:
- SIMPLEST wins. A single attribute selector like [data-testid="job-card"] is better than a multi-level path like li > div > [data-testid="job-card"]. Do NOT add parent/ancestor selectors unless the target is ambiguous without them.
- For data-testid/data-id with DYNAMIC values (e.g. data-testid="card-123"), use prefix matching: [data-testid^="card-"]
- For data-testid with STATIC values (e.g. data-testid="job-card"), use exact: [data-testid="job-card"]
- Prefer semantic HTML: article, section, h2, h3 over div
- NEVER use hashed/generated classes: sc-*, css-*, random 5-8 char strings like "fJyWhK"
- Max 2 levels deep. One level is best.
- The "url" selector should target an <a> element (we extract its href attribute)
- If the page has NO job listings visible, return {{"error": "no job listings found"}}

Return ONLY valid JSON, no explanation, no markdown.

PAGE HTML:
{page_html}"""


class CssSelectorExtractor:
    """Phase 2: send cleaned page HTML to LLM for card detection + selector generation.

    LLM client is injected for testability.
    """

    def __init__(self, llm_client) -> None:
        self._client = llm_client

    def extract(self, intel: dict, plan: dict) -> list[dict]:
        """Returns jobs extracted via LLM-generated CSS selectors.

        Also stores generated selectors back into plan['extraction'] for logging.
        """
        full_html = intel.get("full_html", "")
        if not full_html:
            log.warning("No page HTML captured")
            return []

        cleaned = clean_page_html(full_html)
        log.info("Page HTML: %s -> %s chars", f"{len(full_html):,}", f"{len(cleaned):,}")

        prompt = _FULL_PAGE_SELECTOR_PROMPT.replace("\n\nPAGE HTML:\n{page_html}", "")

        try:
            t0 = time.time()
            # Security: instructions in system message, untrusted HTML in user message
            raw = self._client.chat(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": cleaned},
                ],
                max_output_tokens=4096,
            )
            elapsed = time.time() - t0
        except Exception as e:
            log.error("LLM_ERROR in Phase 2: %s", e)
            return []

        log.info("Phase 2 LLM: %d chars, %.1fs", len(raw), elapsed)

        try:
            selectors = extract_json(raw)
        except Exception as e:
            log.error("PARSE_ERROR in Phase 2: %s | raw: %s", e, raw[:500])
            return []

        if "error" in selectors:
            log.warning("LLM: %s", selectors["error"])
            return []

        # Store selectors back into plan for pipeline logging
        plan["extraction"] = selectors
        log.info("Selectors: %s", selectors)

        # Apply selectors to the ORIGINAL full_html (not cleaned)
        soup = BeautifulSoup(full_html, "html.parser")
        card_sel = selectors.get("job_card", "NONE")
        try:
            cards = soup.select(card_sel)
        except Exception as e:
            log.error("Invalid card selector '%s': %s", card_sel, e)
            return []

        log.info("Matched %d cards", len(cards))

        jobs: list[dict] = []
        for card in cards:
            job: dict = {}
            for field in ("title", "salary", "description", "location", "url"):
                sel = selectors.get(field)
                if not sel or sel == "null":
                    job[field] = None
                    continue
                try:
                    el = card.select_one(sel)
                except Exception:
                    job[field] = None
                    continue
                if el:
                    job[field] = el.get("href") if field == "url" else el.get_text(strip=True)
                else:
                    job[field] = None
            jobs.append(job)

        log.debug("[smartextract] extractor: css_selectors, jobs found: %d", len(jobs))
        if jobs:
            log.debug("[smartextract] sample: %s @ %s", (jobs[0].get("title") or "?")[:40], jobs[0].get("company", "?"))
        return jobs
