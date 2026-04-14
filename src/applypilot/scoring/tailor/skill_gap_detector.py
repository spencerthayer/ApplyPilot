"""Deterministic skill-gap detection (no LLM)."""

from applypilot.scoring.tailor.keyword_extractor import extract_jd_keywords

__all__ = ["check_skill_gaps"]


def check_skill_gaps(jd_text: str, tailored_text: str) -> dict:
    """Compare JD keywords against tailored resume — no LLM call."""
    jd_keywords = extract_jd_keywords(jd_text)
    resume_lower = tailored_text.lower()
    matched = {kw for kw in jd_keywords if kw in resume_lower}
    missing = jd_keywords - matched
    return {
        "jd_keywords": len(jd_keywords),
        "matched": sorted(matched),
        "missing": sorted(missing),
        "coverage": round(len(matched) / len(jd_keywords), 2) if jd_keywords else 1.0,
    }
