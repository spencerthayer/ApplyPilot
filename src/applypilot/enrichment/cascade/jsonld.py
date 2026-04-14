"""Tier 1: JSON-LD JobPosting extraction — zero LLM tokens."""

from __future__ import annotations

from applypilot.enrichment.cascade.html_utils import clean_description


def extract_from_json_ld(intel: dict) -> dict | None:
    """Extract description and apply URL from JSON-LD JobPosting.

    Returns {"full_description": str, "application_url": str|None} or None.
    """

    def _find_job_posting(data):
        if isinstance(data, dict):
            if data.get("@type") == "JobPosting":
                return data
            if "@graph" in data and isinstance(data["@graph"], list):
                for item in data["@graph"]:
                    result = _find_job_posting(item)
                    if result:
                        return result
        elif isinstance(data, list):
            for item in data:
                result = _find_job_posting(item)
                if result:
                    return result
        return None

    for ld in intel.get("json_ld", []):
        posting = _find_job_posting(ld)
        if not posting:
            continue

        desc = posting.get("description", "")
        if not desc:
            continue

        desc_clean = clean_description(desc)
        if len(desc_clean) < 50:
            continue

        apply_url = None
        if posting.get("directApply"):
            apply_url = posting.get("url")
        if not apply_url:
            contact = posting.get("applicationContact")
            if isinstance(contact, dict):
                apply_url = contact.get("url")
        if not apply_url:
            apply_url = posting.get("url")

        return {"full_description": desc_clean, "application_url": apply_url}

    return None
