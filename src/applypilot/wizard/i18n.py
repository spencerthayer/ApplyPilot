"""Multi-language input normalization (INIT-06).

Detects non-English user input and normalizes to English via LLM.
Used during profile creation to accept input in 10+ languages.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Quick heuristic: if >30% of chars are non-ASCII, likely non-English
_NON_ASCII_THRESHOLD = 0.3

_NORMALIZE_PROMPT = """\
The following text is user input that may be in a non-English language.
Translate it to natural, professional English. Preserve all proper nouns, \
company names, technical terms, and numbers exactly as-is.
If the text is already in English, return it unchanged.

Input:
{text}

Return ONLY the English translation, nothing else."""


def needs_normalization(text: str) -> bool:
    """Heuristic check if text likely contains non-English content."""
    if not text or len(text) < 5:
        return False
    non_ascii = sum(1 for c in text if ord(c) > 127)
    return non_ascii / len(text) > _NON_ASCII_THRESHOLD


def normalize_to_english(text: str) -> str:
    """Normalize non-English text to English via LLM. Returns original if already English."""
    if not needs_normalization(text):
        return text
    try:
        from applypilot.llm import get_client

        client = get_client(tier="cheap")
        result = client.chat(
            [{"role": "user", "content": _NORMALIZE_PROMPT.format(text=text)}],
            max_output_tokens=2048,
        )
        normalized = result.strip()
        if normalized:
            log.info("Normalized non-English input (%d chars -> %d chars)", len(text), len(normalized))
            return normalized
    except Exception as e:
        log.warning("Language normalization failed, using original: %s", e)
    return text


def normalize_resume_fields(resume_data: dict) -> dict:
    """Normalize key resume fields to English in-place."""
    # Normalize work highlights
    for job in resume_data.get("work", []):
        highlights = job.get("highlights", [])
        for i, h in enumerate(highlights):
            if needs_normalization(h):
                highlights[i] = normalize_to_english(h)
        if summary := job.get("summary"):
            if needs_normalization(summary):
                job["summary"] = normalize_to_english(summary)

    # Normalize education
    for edu in resume_data.get("education", []):
        for field in ("studyType", "area"):
            if val := edu.get(field):
                if needs_normalization(val):
                    edu[field] = normalize_to_english(val)

    # Normalize top-level summary
    if basics := resume_data.get("basics"):
        if summary := basics.get("summary"):
            if needs_normalization(summary):
                basics["summary"] = normalize_to_english(summary)

    return resume_data
