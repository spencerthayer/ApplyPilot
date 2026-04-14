"""Text sanitization and tokenization helpers."""

from __future__ import annotations

import re

from applypilot.resume.extraction import get_profile_skill_keywords


def sanitize_text(text: str) -> str:
    """Auto-fix common LLM output issues instead of rejecting."""
    text = text.replace(" \u2014 ", ", ").replace("\u2014", ", ")
    text = text.replace("\u2013", "-")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    return text.strip()


def tokenize_words(text: str) -> set[str]:
    """Extract lowercase word tokens from text."""
    return set(re.findall(r"[a-z]{2,}", text.lower()))


def build_skills_set(profile: dict) -> set[str]:
    """Build the set of allowed skills from the normalized profile skills."""
    return {skill.lower().strip() for skill in get_profile_skill_keywords(profile)}
