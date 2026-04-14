"""STAR bullet format validation (INIT-21).

Validates bullets match: Action verb + Context/Task + Method + Result.
"""

from __future__ import annotations

import re

# Minimum: starts with action verb, contains a result indicator
_RESULT_PATTERNS = re.compile(
    r"\d+[%KMx]|reduc|increas|improv|achiev|deliver|enabl|saving|cutting|boost|generat",
    re.IGNORECASE,
)
_ACTION_VERB_PATTERN = re.compile(r"^[A-Z][a-z]+(?:ed|ing|s|e)?\b")


def validate_star(bullet: str) -> dict:
    """Validate a single bullet against STAR format. Returns {valid, issues}."""
    issues = []
    words = bullet.split()

    if not words:
        return {"valid": False, "issues": ["Empty bullet"]}

    # Action verb check
    if not _ACTION_VERB_PATTERN.match(bullet):
        issues.append("Should start with a strong action verb (e.g., Built, Designed, Reduced)")

    # Result/impact check
    if not _RESULT_PATTERNS.search(bullet):
        issues.append("Missing measurable result — add metrics or impact")

    # Length check
    if len(words) < 8:
        issues.append("Too short — add context and method")
    elif len(words) > 30:
        issues.append("Too long — condense to 12-22 words")

    return {"valid": len(issues) == 0, "issues": issues}


def validate_all_bullets(resume: dict) -> list[dict]:
    """Validate all work bullets. Returns list of {company, bullet, valid, issues}."""
    results = []
    for job in resume.get("work", []):
        company = job.get("name", "")
        for bullet in job.get("highlights", []):
            v = validate_star(bullet)
            if not v["valid"]:
                results.append({"company": company, "bullet": bullet[:80], **v})
    return results
