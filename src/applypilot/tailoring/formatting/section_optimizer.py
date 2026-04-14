"""Optimize section order for job targeting."""

from typing import List
from applypilot.intelligence.models import JobIntelligence, MatchAnalysis


class SectionOrderOptimizer:
    DEFAULT_ORDER = ["HEADER", "SUMMARY", "SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION"]

    ROLE_ORDERS = {
        "technical": ["HEADER", "SUMMARY", "SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION"],
        "executive": ["HEADER", "SUMMARY", "EXPERIENCE", "SKILLS", "EDUCATION"],
        "academic": ["HEADER", "EDUCATION", "RESEARCH", "PUBLICATIONS", "EXPERIENCE"],
    }

    def optimize(self, job_intel: JobIntelligence, match_analysis: MatchAnalysis) -> List[str]:
        role_type = self._detect_role_type(job_intel)
        order = self.ROLE_ORDERS.get(role_type, self.DEFAULT_ORDER).copy()

        if any("education" in g.requirement.lower() for g in match_analysis.gaps):
            order = self._move_up(order, "EDUCATION")

        return order

    def _detect_role_type(self, job_intel: JobIntelligence) -> str:
        title = job_intel.title.lower()
        if any(w in title for w in ["staff", "principal", "architect"]):
            return "technical"
        elif any(w in title for w in ["cto", "vp", "director"]):
            return "executive"
        elif "phd" in title or "research" in title:
            return "academic"
        return "technical"

    def _move_up(self, order: List[str], section: str) -> List[str]:
        if section in order:
            order.remove(section)
            order.insert(min(2, len(order)), section)
        return order
