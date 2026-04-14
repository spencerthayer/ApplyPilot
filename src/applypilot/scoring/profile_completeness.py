"""Profile completeness score (INIT-05).

Computes 0-10 score based on field coverage, depth, quantification, skill breadth.
"""

from __future__ import annotations

import re


def compute_completeness(resume: dict) -> dict:
    """Compute profile completeness score with per-section breakdown and tips."""
    basics = resume.get("basics", {})
    work = resume.get("work", [])
    skills = resume.get("skills", [])
    education = resume.get("education", [])
    projects = resume.get("projects", [])

    scores = {}
    tips = []

    # Contact info (1 point)
    contact_fields = [basics.get("name"), basics.get("email"), basics.get("phone")]
    scores["contact"] = min(sum(1 for f in contact_fields if f) / 3, 1.0)
    if scores["contact"] < 1:
        tips.append("Add missing contact info (name, email, phone)")

    # Summary (1 point)
    summary = basics.get("summary", "")
    scores["summary"] = min(len(summary) / 100, 1.0)
    if not summary:
        tips.append("Add a professional summary")
    elif len(summary) < 50:
        tips.append("Expand summary to 2-3 sentences with metrics")

    # Work experience depth (3 points)
    total_bullets = sum(len(j.get("highlights", [])) for j in work)
    scores["experience"] = min(total_bullets / 12, 1.0) * 3
    if not work:
        tips.append("Add work experience")
    elif total_bullets < 6:
        tips.append(f"Add more bullets — you have {total_bullets}, aim for 12+")

    # Quantification — bullets with numbers (1.5 points)
    quantified = sum(1 for j in work for b in j.get("highlights", []) if re.search(r"\d+[%KMx]|\d{2,}", b))
    quant_ratio = quantified / max(total_bullets, 1)
    scores["quantification"] = min(quant_ratio / 0.5, 1.0) * 1.5
    if quant_ratio < 0.3:
        tips.append(f"Only {quantified}/{total_bullets} bullets have metrics — add numbers to show impact")

    # Skills breadth (1.5 points)
    total_skills = sum(len(s.get("keywords", [])) for s in skills)
    scores["skills"] = min(total_skills / 15, 1.0) * 1.5
    if total_skills < 5:
        tips.append("Add more skills — aim for 15+ across categories")

    # Education (1 point)
    scores["education"] = 1.0 if education else 0.0
    if not education:
        tips.append("Add education")

    # Projects/extras (1 point)
    scores["projects"] = min(len(projects) / 2, 1.0)
    if not projects:
        tips.append("Add 1-2 projects to showcase independent work")

    total = sum(scores.values())
    return {
        "score": round(min(total, 10.0), 1),
        "sections": {k: round(v, 1) for k, v in scores.items()},
        "tips": tips[:5],
    }
