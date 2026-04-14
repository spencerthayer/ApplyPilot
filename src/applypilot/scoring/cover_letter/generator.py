"""Cover letter generator — LLM-powered with retry and validation."""

from __future__ import annotations

import logging

from applypilot.llm import get_client
from applypilot.scoring.cover_letter.prompt_builder import build_cover_letter_prompt
from applypilot.scoring.validator import sanitize_text, validate_cover_letter

log = logging.getLogger(__name__)


def _strip_preamble(text: str) -> str:
    """Remove LLM preamble before 'Dear Hiring Manager,' if present."""
    dear_idx = text.lower().find("dear")
    return text[dear_idx:] if dear_idx > 0 else text


def generate_cover_letter(
        resume_text: str,
        job: dict,
        profile: dict,
        max_retries: int = 3,
        validation_mode: str = "normal",
) -> str:
    """Generate a cover letter with fresh context on each retry + auto-sanitize."""
    job_text = (
        f"TITLE: {job['title']}\nCOMPANY: {job['site']}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{(job.get('full_description') or '')[:6000]}"
    )

    avoid_notes: list[str] = []
    letter = ""
    client = get_client(tier="premium")
    cl_prompt_base = build_cover_letter_prompt(profile)

    for attempt in range(max_retries + 1):
        prompt = cl_prompt_base
        if avoid_notes:
            prompt += "\n\n## AVOID THESE ISSUES:\n" + "\n".join(f"- {n}" for n in avoid_notes[-5:])

        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": f"RESUME:\n{resume_text}\n\n---\n\nTARGET JOB:\n{job_text}\n\nWrite the cover letter:",
            },
        ]

        letter = client.chat(messages, max_output_tokens=10000)
        letter = sanitize_text(letter)
        letter = _strip_preamble(letter)

        validation = validate_cover_letter(letter, mode=validation_mode)
        if validation["passed"]:
            return letter

        avoid_notes.extend(validation["errors"])
        log.debug("Cover letter attempt %d/%d failed: %s", attempt + 1, max_retries + 1, validation["errors"])

    return letter
