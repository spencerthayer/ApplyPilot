"""Job description parser using LLM for structured extraction."""

import json
import logging
import re

from applypilot.intelligence.models import (
    JobIntelligence,
    Requirement,
    SeniorityLevel,
    Skill,
)
from applypilot.llm import get_client

log = logging.getLogger(__name__)

JD_PARSE_PROMPT = """You are a job description analyzer. Extract structured information from the job description below.

Output JSON:
{{
    "seniority": "junior|mid|senior|staff|principal",
    "requirements": [
        {{"text": "requirement", "type": "must_have|nice_to_have", "category": "technical|experience|education"}}
    ],
    "skills": [
        {{"name": "skill", "required": true|false, "proficiency": "expert|proficient|familiar|null"}}
    ],
    "key_responsibilities": ["resp1", "resp2"],
    "red_flags": [],
    "company_context": {{"industry": "", "stage": "startup|growth|enterprise"}}
}}

Job Description:
{job_description}
"""


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM response, handling markdown fences."""
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code fences
    match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding first { ... } block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse JSON from LLM response: {text[:200]}")


class JobDescriptionParser:
    """Parse job descriptions into structured JobIntelligence using LLM."""

    def __init__(self) -> None:
        self.client = get_client()

    def parse(self, job: dict) -> JobIntelligence:
        """Parse a job dict with 'title', 'company', 'description' keys.

        Returns a fully populated JobIntelligence dataclass.
        """
        description = job.get("description", "")
        if not description:
            raise ValueError("Job dict must contain a non-empty 'description' key")

        prompt = JD_PARSE_PROMPT.format(job_description=description)
        response = self.client.ask(prompt, temperature=0.1, max_tokens=2048)

        data = _extract_json(response)
        log.debug("Parsed JD data: %s", json.dumps(data, indent=2)[:500])

        return JobIntelligence(
            title=job.get("title", "Unknown"),
            company=job.get("company", "Unknown"),
            seniority=SeniorityLevel(data.get("seniority", "mid")),
            requirements=[Requirement(**r) for r in data.get("requirements", [])],
            skills=[Skill(**s) for s in data.get("skills", [])],
            key_responsibilities=data.get("key_responsibilities", []),
            red_flags=data.get("red_flags", []),
            company_context=data.get("company_context", {}),
        )


def parse_jd(job: dict) -> JobIntelligence:
    """Convenience function — parse a job description into structured data."""
    return JobDescriptionParser().parse(job)
