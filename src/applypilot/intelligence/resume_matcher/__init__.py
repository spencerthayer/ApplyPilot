"""Match resume against job intelligence for gap analysis."""

import json
import logging
import re

from applypilot.intelligence.models import Gap, JobIntelligence, MatchAnalysis
from applypilot.llm import get_client

log = logging.getLogger(__name__)

MATCH_PROMPT = """Analyze how well the resume matches the job requirements.

RESUME:
{resume_text}

JOB REQUIREMENTS:
{requirements}

Output JSON:
{{
    "overall_score": 7.5,
    "strengths": ["strength 1"],
    "gaps": [
        {{"requirement": "gap desc", "severity": "critical|major|minor", "suggestion": "how to fix"}}
    ],
    "recommendations": ["rec 1"],
    "bullet_priorities": {{"bullet1": 10}}
}}
"""


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM response, handling markdown fences."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse JSON from LLM response: {text[:200]}")


class ResumeMatcher:
    """Analyze fit between a resume and job requirements."""

    def __init__(self) -> None:
        self.client = get_client()

    def analyze(self, resume_text: str, job_intel: JobIntelligence) -> MatchAnalysis:
        """Analyze match between resume text and job intelligence.

        Returns a MatchAnalysis with score, strengths, gaps, and recommendations.
        """
        requirements_text = "\n".join(f"- [{r.type}] {r.text}" for r in job_intel.requirements)
        if not requirements_text:
            requirements_text = f"Title: {job_intel.title}, Company: {job_intel.company}"

        prompt = MATCH_PROMPT.format(
            resume_text=resume_text[:4000],
            requirements=requirements_text,
        )

        response = self.client.ask(prompt, temperature=0.2, max_tokens=2048)
        data = _extract_json(response)

        return MatchAnalysis(
            overall_score=float(data.get("overall_score", 5.0)),
            strengths=data.get("strengths", []),
            gaps=[Gap(**g) for g in data.get("gaps", [])],
            recommendations=data.get("recommendations", []),
            bullet_priorities=data.get("bullet_priorities", {}),
        )
