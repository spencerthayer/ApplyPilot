"""LLM strategy planner: decides which extraction method to use.

Single responsibility: given page intelligence, ask the LLM which
extraction strategy (json_ld, api_response, css_selectors) is best.
LLM client is injected via constructor for testability.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Protocol, runtime_checkable

from applypilot.discovery.smartextract.json_utils import extract_json

log = logging.getLogger(__name__)


@runtime_checkable
class LLMClient(Protocol):
    """Minimal protocol for LLM interaction — matches applypilot.llm.LLMClient."""

    def chat(self, messages: list[dict], **kwargs) -> str: ...


# -- Strategy briefing (lightweight, no raw DOM) --


def format_strategy_briefing(intel: dict) -> str:
    """Build a concise briefing for the LLM strategy selection prompt."""
    sections: list[str] = []
    sections.append(f"PAGE: {intel['url']}")
    sections.append(f"TITLE: {intel['page_title']}")

    # JSON-LD
    if intel["json_ld"]:
        job_postings = [j for j in intel["json_ld"] if isinstance(j, dict) and j.get("@type") == "JobPosting"]
        other = [j for j in intel["json_ld"] if not (isinstance(j, dict) and j.get("@type") == "JobPosting")]
        if job_postings:
            sections.append(f"\nJSON-LD: {len(job_postings)} JobPosting entries found (usable!)")
            sections.append(f"First JobPosting:\n{json.dumps(job_postings[0], indent=2)[:3000]}")
        else:
            sections.append("\nJSON-LD: NO JobPosting entries (json_ld strategy will NOT work)")
        if other:
            types = [j.get("@type", "?") if isinstance(j, dict) else "?" for j in other]
            sections.append(f"Other JSON-LD types (NOT job data): {types}")
    else:
        sections.append("\nJSON-LD: none")

    # API responses
    if intel["api_responses"]:
        sections.append(f"\nAPI RESPONSES INTERCEPTED: {len(intel['api_responses'])} calls")
        for resp in intel["api_responses"]:
            sections.append(f"\n  URL: {resp['url']}")
            sections.append(
                f"  Status: {resp['status']} | Size: {resp['size']:,} chars | Type: {resp.get('type', '?')}"
            )
            if "first_item_keys" in resp:
                sections.append(f"  Item keys: {resp['first_item_keys']}")
                sections.append(f"  Sample: {json.dumps(resp.get('first_item_sample', {}), indent=2)[:1000]}")
            if "keys" in resp:
                sections.append(f"  Object keys: {resp['keys']}")
            for k, v in resp.items():
                if k.startswith("nested_"):
                    arr_name = k.replace("nested_", "")
                    sections.append(f"  .{arr_name}: array of {v['count']} items")
                    sections.append(f"    Item keys: {v['first_item_keys']}")
                    sections.append(f"    Sample: {json.dumps(v.get('first_item_sample', {}), indent=2)[:1000]}")
                    for sk, sv in v.items():
                        if sk.startswith("first_item.") and isinstance(sv, dict):
                            sub_name = sk.replace("first_item.", "")
                            if "count" in sv:
                                sections.append(f"    .{arr_name}[0].{sub_name}: array of {sv['count']} items")
                                sections.append(f"      Item keys: {sv['first_item_keys']}")
                                sections.append(
                                    f"      Sample: {json.dumps(sv.get('first_item_sample', {}), indent=2)[:1500]}"
                                )
                            elif "keys" in sv:
                                sections.append(f"    .{arr_name}[0].{sub_name}: object with keys {sv['keys']}")
                                sections.append(f"      Sample: {json.dumps(sv.get('sample', {}), indent=2)[:1500]}")
    else:
        sections.append("\nAPI RESPONSES: none intercepted")

    # data-testid
    if intel["data_testids"]:
        sections.append(f"\nDATA-TESTID ATTRIBUTES: {len(intel['data_testids'])} elements")
        for dt in intel["data_testids"][:15]:
            text_preview = dt["text"].replace("\n", " ")[:60]
            sections.append(f'  <{dt["tag"]} data-testid="{dt["testid"]}"> {text_preview}')
    else:
        sections.append("\nDATA-TESTID: none found")

    # DOM stats
    stats = intel.get("dom_stats", {})
    sections.append(
        f"\nDOM STATS: {stats.get('total_elements', '?')} elements, "
        f"{stats.get('links', '?')} links, {stats.get('headings', '?')} headings, "
        f"{stats.get('tables', '?')} tables, {stats.get('articles', '?')} articles, "
        f"{stats.get('has_data_ids', '?')} data-id elements"
    )

    # Card candidates
    if intel["card_candidates"]:
        sections.append(f"\nREPEATING ELEMENTS DETECTED: {len(intel['card_candidates'])} candidate groups")
        for i, cand in enumerate(intel["card_candidates"]):
            sections.append(
                f"  [{i}] parent={cand['parent_selector']} child={cand['child_selector']} "
                f"count={cand['total_children']} with_text={cand['with_text']} with_links={cand['with_links']}"
            )
    else:
        sections.append("\nREPEATING ELEMENTS: none detected")

    return "\n".join(sections)


# -- Strategy prompt --

STRATEGY_PROMPT = """You are analyzing a job listings page to pick the best extraction strategy.

Below is a lightweight intelligence briefing -- JSON-LD data, intercepted API responses, data-testid attributes, and DOM statistics. NO raw DOM HTML is included.

Pick the BEST strategy:

1. "json_ld" -- ONLY if briefing shows JobPosting JSON-LD entries (it will say "usable!")
2. "api_response" -- ONLY if an intercepted API response has job-like fields (name, title, salary, description, location, slug)
3. "css_selectors" -- when neither JSON-LD nor API data has job data

HOW TO THINK:
- If the briefing says "JSON-LD: NO JobPosting entries" or "json_ld strategy will NOT work", do NOT pick json_ld.
- For api_response: "url_pattern" must be a substring that matches one of the INTERCEPTED API URLs listed above (not the page URL!). Copy a unique part of the API URL.
- For api_response: "items_path" must point to the ARRAY of items, not a single item. Use dot notation with [n] ONLY for traversing into a specific index to reach an inner array. Example: if data is {{"results": [{{"hits": [...]}}]}}, items_path is "results[0].hits" to reach the hits array.
- For api_response: field paths (title, salary, etc.) are RELATIVE TO EACH ITEM in the array. If items are nested objects like {{"_source": {{"Title": "..."}}}}, use "_source.Title" for the title field.
- For css_selectors: just return {{"strategy":"css_selectors","reasoning":"...","extraction":{{}}}} -- selectors will be generated in a separate focused step.

Return ONLY valid JSON:

For json_ld:
{{"strategy":"json_ld","reasoning":"...","extraction":{{"title":"title","salary":"baseSalary_path_or_null","description":"description","location":"jobLocation[0].address.addressCountry","url":"url_field"}}}}

For api_response:
{{"strategy":"api_response","reasoning":"...","extraction":{{"url_pattern":"actual.url.substring","items_path":"path.to.the.array","title":"field_in_each_item","salary":"salary_field_or_null","description":"description_field_or_null","location":"location_path","url":"url_field"}}}}

For css_selectors:
{{"strategy":"css_selectors","reasoning":"...","extraction":{{}}}}

Keep reasoning under 20 words. No explanation, no markdown, no code fences.

INTELLIGENCE BRIEFING:
{briefing}"""


class StrategyPlanner:
    """Asks the LLM which extraction strategy to use for a page.

    Dependency injection: LLM client is passed in, not imported globally.
    """

    def __init__(self, llm_client: LLMClient) -> None:
        self._client = llm_client

    def plan(self, intel: dict, site_name: str = "?") -> dict:
        """Return an extraction plan dict with 'strategy' and 'extraction' keys."""
        briefing = format_strategy_briefing(intel)
        log.debug("[smartextract] %s — strategy briefing: %d chars", site_name, len(briefing))

        # Security: instructions in system message, untrusted page data in user message
        system_msg = STRATEGY_PROMPT.replace("\n\nINTELLIGENCE BRIEFING:\n{briefing}", "")
        t0 = time.time()
        raw = self._client.chat(
            [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": f"INTELLIGENCE BRIEFING:\n{briefing}"},
            ],
            max_output_tokens=4096,
        )
        elapsed = time.time() - t0

        plan = extract_json(raw)
        strategy = plan.get("strategy", "?")
        confidence = plan.get("reasoning", "?")
        log.debug(
            "[smartextract] %s — LLM strategy: %s, confidence: %s (%.1fs)", site_name, strategy, confidence, elapsed
        )
        return plan


# -- API response judge --

_JUDGE_PROMPT = """You are filtering intercepted API responses from a job listings website.
Decide if this API response contains actual job listing data (titles, companies, locations, etc).

API Response Summary:
  URL: {url}
  Status: {status}
  Size: {size} chars
  Type: {type}
  Keys/Fields: {fields}
  Sample: {sample}

Is this job listing data? Answer in under 10 words. Return ONLY valid JSON:
{{"relevant": true, "reason": "job objects with title/company"}}
or
{{"relevant": false, "reason": "auth endpoint"}}

No explanation, no markdown, no thinking."""


def judge_api_responses(api_responses: list[dict], llm_client: LLMClient) -> list[dict]:
    """Use the LLM to filter API responses, keeping only job-relevant ones."""
    if not api_responses:
        return []

    relevant: list[dict] = []
    for resp in api_responses:
        fields = ""
        sample = ""
        resp_type = resp.get("type", "unknown")
        if "first_item_keys" in resp:
            fields = str(resp["first_item_keys"])
            sample = json.dumps(resp.get("first_item_sample", {}), indent=2)[:500]
        elif "keys" in resp:
            fields = str(resp["keys"])
            for k, v in resp.items():
                if k.startswith("nested_"):
                    fields += f"\n  .{k.replace('nested_', '')}: {v.get('count', '?')} items, keys={v.get('first_item_keys', '?')}"
                    sample = json.dumps(v.get("first_item_sample", {}), indent=2)[:500]
        else:
            fields = "no structured data"

        # Strip non-printable / high-codepoint chars that trigger Gemini safety blocks
        sample = "".join(c for c in sample if c.isprintable() and ord(c) < 0x10000)

        # Security: instructions in system message, untrusted API data in user message
        system_prompt = (
            "You are filtering intercepted API responses from a job listings website.\n"
            "Decide if this API response contains actual job listing data (titles, companies, locations, etc).\n"
            "Answer in under 10 words. Return ONLY valid JSON:\n"
            '{"relevant": true, "reason": "job objects with title/company"}\n'
            "or\n"
            '{"relevant": false, "reason": "auth endpoint"}\n'
            "No explanation, no markdown, no thinking."
        )
        user_content = (
            f"API Response Summary:\n"
            f"  URL: {resp.get('url', '?')[:200]}\n"
            f"  Status: {resp.get('status', '?')}\n"
            f"  Size: {resp.get('size', '?')} chars\n"
            f"  Type: {resp_type}\n"
            f"  Keys/Fields: {fields}\n"
            f"  Sample: {sample or 'n/a'}"
        )

        try:
            raw = llm_client.chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                max_output_tokens=1024,
            )
            verdict = extract_json(raw)
            is_relevant = verdict.get("relevant", False)
            reason = verdict.get("reason", "?")
            log.info("Judge: %s -> %s (%s)", resp.get("url", "?")[:80], "KEEP" if is_relevant else "DROP", reason)
            if is_relevant:
                relevant.append(resp)
        except Exception as e:
            log.warning("Judge ERROR for %s: %s -- keeping", resp.get("url", "?")[:80], e)
            relevant.append(resp)

    return relevant
