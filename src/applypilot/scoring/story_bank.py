"""Interview Story Bank — STAR+R stories accumulated across evaluations.

Each tailoring run can generate STAR+R stories mapped to JD requirements.
Stories accumulate over time — 5-10 master stories that answer any behavioral question.

STAR+R = Situation, Task, Action, Result + Reflection (what was learned).
The Reflection signals seniority — juniors describe what happened, seniors extract lessons.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class Story:
    id: str
    requirement: str  # JD requirement this story answers
    situation: str
    task: str
    action: str
    result: str
    reflection: str  # what was learned / what would be done differently
    source_bullet: str  # original resume bullet this was derived from
    company: str
    use_count: int = 0
    tags: list[str] | None = None


def generate_stories(resume_bullets: list[dict], jd_requirements: list[str]) -> list[Story]:
    """Generate STAR+R stories from resume bullets mapped to JD requirements.

    Uses the bullet text to decompose into STAR+R components.
    No LLM needed — rule-based extraction from well-structured bullets.
    """
    import hashlib

    stories = []
    for req in jd_requirements[:6]:
        # Find best matching bullet
        req_lower = req.lower()
        best_bullet = None
        best_score = 0
        for b in resume_bullets:
            text = b.get("text", b) if isinstance(b, dict) else str(b)
            company = b.get("company", "") if isinstance(b, dict) else ""
            words = set(text.lower().split())
            overlap = len(words & set(req_lower.split()))
            if overlap > best_score:
                best_score = overlap
                best_bullet = {"text": text, "company": company}

        if not best_bullet or best_score < 2:
            continue

        text = best_bullet["text"]
        sid = hashlib.sha256(f"{req}:{text}".encode()).hexdigest()[:10]

        stories.append(
            Story(
                id=sid,
                requirement=req[:100],
                situation=f"At {best_bullet['company']}" if best_bullet["company"] else "In my previous role",
                task=req[:100],
                action=text[:200],
                result=_extract_result(text),
                reflection=_generate_reflection(text),
                source_bullet=text[:200],
                company=best_bullet.get("company", ""),
                tags=[w for w in req_lower.split() if len(w) > 3][:5],
            )
        )

    return stories


def _extract_result(bullet: str) -> str:
    """Extract the result/metric portion of a bullet."""
    import re

    match = re.search(r"(\d+[%KMx][\w\s]*|reducing[^,\.]*|cutting[^,\.]*|saving[^,\.]*|achieving[^,\.]*)", bullet, re.I)
    return match.group(0) if match else "Delivered successfully"


def _generate_reflection(bullet: str) -> str:
    """Generate a reflection based on the bullet content."""
    if "independently" in bullet.lower() or "designed" in bullet.lower():
        return "Learned the value of owning a system end-to-end — from design through production monitoring"
    if "migrat" in bullet.lower() or "legacy" in bullet.lower():
        return "Learned that incremental migration with feature flags reduces risk vs big-bang rewrites"
    if "automat" in bullet.lower() or "script" in bullet.lower():
        return "Learned to identify repetitive manual work early and invest in automation"
    return "Reinforced the importance of measuring impact with concrete metrics"


# Big Three mapping — which stories answer the universal interview questions
BIG_THREE = {
    "tell_me_about_yourself": "Combine 2-3 highest-impact stories into a narrative arc",
    "most_impactful_project": "Pick the story with the largest quantified result",
    "conflict_resolution": "Pick a story with a Reflection about what was learned",
}
