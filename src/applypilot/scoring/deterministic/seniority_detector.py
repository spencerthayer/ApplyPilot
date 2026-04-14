"""Seniority detection — re-exports from title_matcher."""

from applypilot.scoring.deterministic.title_matcher import (
    SENIORITY_PATTERNS,
    seniority_from_text,
)

__all__ = ["SENIORITY_PATTERNS", "seniority_from_text"]
