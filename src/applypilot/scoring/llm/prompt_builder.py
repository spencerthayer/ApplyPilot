"""LLM scoring prompt construction."""

__all__ = [
    "SCORE_PROMPT",
    "SCORING_RESPONSE_FORMAT",
    "format_scoring_profile_for_prompt",
]

SCORE_PROMPT = """You are a job-fit scoring calibrator.

You will receive:
1) Candidate resume profile and resume text.
2) Job posting context focused on requirements and responsibilities.
3) Deterministic baseline signals from an offline scorer.

Your job:
- Re-evaluate fit quality and provide a calibrated score.
- Respect evidence in requirements over generic title matching.
- Keep reasoning concise and grounded in the provided content.

Return JSON ONLY with this schema:
{
  "score": 1-10 integer,
  "confidence": 0.0-1.0 number,
  "why_short": "3-9 word summary",
  "matched_skills": ["..."],
  "missing_requirements": ["..."],
  "reasoning": "full rationale with key evidence"
}
"""

SCORING_RESPONSE_FORMAT = {"type": "json_object"}


def format_scoring_profile_for_prompt(scoring_profile: dict) -> str:
    return (
        f"Target role: {scoring_profile.get('target_role') or 'N/A'}\n"
        f"Years experience: {scoring_profile.get('years_total') or 0}\n"
        f"Recent titles: {', '.join(scoring_profile.get('current_titles') or []) or 'N/A'}\n"
        f"Skills: {', '.join((scoring_profile.get('known_skills') or [])[:40]) or 'N/A'}"
    )
