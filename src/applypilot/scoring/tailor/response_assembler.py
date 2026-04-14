"""Response assembly: JSON extraction, bullet normalisation, and resume text formatting."""

import json
import logging
import re
from typing import Any

from applypilot.scoring.validator import FABRICATION_WATCHLIST, sanitize_text  # noqa: F401

__all__ = [
    "extract_json",
    "normalize_bullet",
    "strip_disallowed_watchlist_skills",
    "assemble_resume_text",
]

logger = logging.getLogger(__name__)


# ── JSON Extraction ───────────────────────────────────────────────────────


def extract_json(raw: str) -> dict:
    """Robustly extract JSON from LLM response (handles fences, preamble).

    Args:
        raw: Raw LLM response text.

    Returns:
        Parsed JSON dict.

    Raises:
        ValueError: If no valid JSON found.
    """
    raw = raw.strip()

    # Direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Markdown fences
    if "```" in raw:
        for part in raw.split("```")[1::2]:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            try:
                return json.loads(part)
            except json.JSONDecodeError:
                continue

    # Find outermost { ... }
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start: end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError("No valid JSON found in LLM response")


def normalize_bullet(bullet: Any) -> str:
    """Normalize a bullet to plain text, stripping embedded JSON metadata."""

    if isinstance(bullet, dict):
        for key in ("text", "bullet", "content", "description"):
            value = bullet.get(key)
            if isinstance(value, str):
                return value.strip()
        return json.dumps(bullet, ensure_ascii=False)

    bullet_str = str(bullet).strip()
    if bullet_str.startswith("{") or bullet_str.startswith("["):
        try:
            parsed = json.loads(bullet_str)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            for key in ("text", "bullet", "content", "description"):
                value = parsed.get(key)
                if isinstance(value, str):
                    return value.strip()
            return json.dumps(parsed, ensure_ascii=False)

    json_start = bullet_str.find(" {")
    if json_start == -1:
        json_start = bullet_str.find("\t{")
    if json_start != -1:
        candidate = bullet_str[:json_start].rstrip()
        remainder = bullet_str[json_start:].strip()
        if remainder.startswith("{") and (
                "variants" in remainder or "tags" in remainder or "role_families" in remainder
        ):
            return candidate
    return bullet_str


def strip_disallowed_watchlist_skills(data: dict, profile: dict) -> list[str]:
    """Remove watchlist skills from generated skill output."""

    skills = data.get("skills")
    if not isinstance(skills, dict):
        return []

    def _normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    # Keep function signature aligned with profile-aware sanitizers even though
    # watchlist terms are always stripped to match validator behavior.
    del profile
    watchlist_norm: set[str] = set()
    for skill in FABRICATION_WATCHLIST:
        if len(skill) <= 2:
            continue
        normalized_skill = _normalize(skill)
        if not normalized_skill:
            continue
        # Avoid collapsing values like "c++" to single-character tokens ("c").
        if len(normalized_skill.replace(" ", "")) <= 2:
            continue
        watchlist_norm.add(normalized_skill)

    removed: list[str] = []

    for key, value in list(skills.items()):
        if isinstance(value, str):
            entries = [part.strip() for part in value.split(",") if part.strip()]
        elif isinstance(value, list):
            entries = [str(part).strip() for part in value if str(part).strip()]
        else:
            entries = [str(value).strip()] if str(value).strip() else []

        kept: list[str] = []
        for entry in entries:
            entry_norm = _normalize(entry)
            if not entry_norm:
                continue
            is_watchlist = any(w in entry_norm for w in watchlist_norm)
            if is_watchlist:
                removed.append(entry)
                continue
            kept.append(entry)

        skills[key] = ", ".join(kept)

    return removed


# ── Resume Assembly (profile-driven header) ──────────────────────────────


def assemble_resume_text(data: dict, profile: dict) -> str:
    """Convert JSON resume data to formatted plain text.

    Delegates to ResumeBuilder (builder pattern) for consistent rendering.
    """
    from applypilot.resume_builder import from_tailored_output

    return from_tailored_output(data, profile).render_text()
