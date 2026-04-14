"""Per-JD laser-matching pipeline (LLD §9, INIT-09).

5-step decision tree for each JD requirement:
  1. Exact match in profile → emphasize
  2. Synonym match → rephrase to JD's exact phrasing for ATS
  3. Adjacent skill in adjacency graph → add with confidence-based hedge
  4. In master profile but not this resume version → add from master
  5. Genuine gap → don't add, track as skill gap in analytics
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from applypilot.intelligence.adjacency_graph.graph import SkillAdjacencyGraph

log = logging.getLogger(__name__)


class MatchType(StrEnum):
    EXACT = "exact"
    SYNONYM = "synonym"
    ADJACENT = "adjacent"
    MASTER_PROFILE = "master_profile"
    GAP = "gap"


@dataclass(frozen=True, slots=True)
class RequirementMatch:
    requirement: str
    match_type: MatchType
    matched_skill: str | None = None
    confidence: float = 0.0
    action: str = ""


_SYNONYM_MAP: dict[str, list[str]] = {
    "k8s": ["kubernetes"],
    "kubernetes": ["k8s"],
    "js": ["javascript"],
    "javascript": ["js"],
    "ts": ["typescript"],
    "typescript": ["ts"],
    "postgres": ["postgresql"],
    "postgresql": ["postgres"],
    "react.js": ["react", "reactjs"],
    "node.js": ["node", "nodejs"],
    "aws": ["amazon web services"],
    "gcp": ["google cloud"],
}


def match_requirement(
        requirement: str,
        resume_skills: set[str],
        profile_skills: set[str],
        graph: SkillAdjacencyGraph,
) -> RequirementMatch:
    """Run the 5-step decision tree for a single JD requirement."""
    req_lower = requirement.lower().strip()

    # Step 1: Exact match
    if req_lower in resume_skills:
        return RequirementMatch(requirement, MatchType.EXACT, req_lower, 1.0, "emphasize")

    # Step 2: Synonym match
    for synonym in _SYNONYM_MAP.get(req_lower, []):
        if synonym in resume_skills:
            return RequirementMatch(requirement, MatchType.SYNONYM, synonym, 0.95, "rephrase to JD phrasing")

    # Step 3: Adjacency graph
    if edge := graph.resolve(req_lower, resume_skills):
        match edge.confidence:
            case c if c >= 0.85:
                return RequirementMatch(requirement, MatchType.ADJACENT, edge.target, c, "add confidently")
            case c if c >= 0.5:
                return RequirementMatch(requirement, MatchType.ADJACENT, edge.target, c, "add with hedge")
            case c:
                return RequirementMatch(requirement, MatchType.ADJACENT, edge.target, c, "ask user")

    # Step 4: In master profile but not current resume
    if req_lower in profile_skills and req_lower not in resume_skills:
        return RequirementMatch(requirement, MatchType.MASTER_PROFILE, req_lower, 0.8, "add from master")

    # Step 5: Genuine gap
    return RequirementMatch(requirement, MatchType.GAP, None, 0.0, "address in cover letter")


def match_all_requirements(
        jd_requirements: list[str],
        resume_skills: set[str],
        profile_skills: set[str],
        graph: SkillAdjacencyGraph | None = None,
) -> list[RequirementMatch]:
    """Match all JD requirements against resume + profile skills."""
    if graph is None:
        try:
            from applypilot.bootstrap import get_app

            graph = get_app().container.skill_graph
        except Exception:
            graph = SkillAdjacencyGraph()
            graph.load_yaml()

    return [match_requirement(req, resume_skills, profile_skills, graph) for req in jd_requirements]


def summarize_matches(matches: list[RequirementMatch]) -> dict:
    """Summarize match results for logging/analytics."""
    by_type = {}
    for m in matches:
        by_type.setdefault(m.match_type.value, []).append(m.requirement)
    coverage = sum(1 for m in matches if m.match_type != MatchType.GAP) / max(len(matches), 1)
    return {
        "total": len(matches),
        "coverage": round(coverage, 2),
        "by_type": {k: len(v) for k, v in by_type.items()},
        "gaps": by_type.get("gap", []),
    }
