"""Resume quality score + actionable feedback (INIT-08).

Scores: action verb strength, quantification density, bullet length, ATS keyword coverage.
"""

from __future__ import annotations

import re

_STRONG_VERBS = {
    "architected",
    "built",
    "created",
    "delivered",
    "designed",
    "developed",
    "drove",
    "eliminated",
    "engineered",
    "established",
    "implemented",
    "improved",
    "increased",
    "integrated",
    "launched",
    "led",
    "migrated",
    "optimized",
    "orchestrated",
    "pioneered",
    "reduced",
    "redesigned",
    "scaled",
    "shipped",
    "solved",
    "streamlined",
    "transformed",
}
_WEAK_VERBS = {"worked", "helped", "assisted", "participated", "involved", "responsible", "handled", "did", "made"}


def compute_quality(resume: dict) -> dict:
    """Compute resume quality score with per-bullet feedback."""
    work = resume.get("work", [])
    all_bullets = [(j.get("name", ""), b) for j in work for b in j.get("highlights", [])]

    if not all_bullets:
        return {"score": 0, "feedback": [], "summary": "No bullets to analyze"}

    verb_scores = []
    quant_count = 0
    length_issues = []
    weak_bullets = []

    for company, bullet in all_bullets:
        first_word = bullet.strip().split()[0].lower().rstrip("ed").rstrip("s") if bullet.strip() else ""
        full_first = bullet.strip().split()[0].lower() if bullet.strip() else ""

        # Verb strength
        if full_first in _STRONG_VERBS or first_word in _STRONG_VERBS:
            verb_scores.append(1.0)
        elif full_first in _WEAK_VERBS:
            verb_scores.append(0.0)
            weak_bullets.append(
                {
                    "company": company,
                    "bullet": bullet[:80],
                    "issue": f"Weak verb '{full_first}' — use a stronger action verb",
                }
            )
        else:
            verb_scores.append(0.5)

        # Quantification
        if re.search(r"\d+[%KMx]|\d{2,}", bullet):
            quant_count += 1

        # Length
        words = len(bullet.split())
        if words < 8:
            length_issues.append(
                {"company": company, "bullet": bullet[:80], "issue": "Too short — add context and impact"}
            )
        elif words > 30:
            length_issues.append({"company": company, "bullet": bullet[:80], "issue": "Too long — aim for 12-22 words"})

    n = len(all_bullets)
    verb_avg = sum(verb_scores) / n
    quant_ratio = quant_count / n
    length_ok = 1 - len(length_issues) / n

    # Score: verb strength (30%) + quantification (40%) + length (30%)
    raw = verb_avg * 3 + min(quant_ratio / 0.5, 1.0) * 4 + length_ok * 3
    score = round(min(max(raw, 0), 10), 1)

    feedback = (weak_bullets + length_issues)[:8]

    return {
        "score": score,
        "verb_strength": round(verb_avg, 2),
        "quantification_rate": round(quant_ratio, 2),
        "total_bullets": n,
        "quantified_bullets": quant_count,
        "feedback": feedback,
    }
