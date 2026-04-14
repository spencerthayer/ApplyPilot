"""Who Variant."""

import logging

log = logging.getLogger(__name__)

# Base instruction for all prompts to preserve metrics
_METRICS_PRESERVATION = (
    "Preserve all numbers and metrics exactly. Do not change, round, or fabricate any numerical values."
)


def generate_who_variant(text: str, client, job_context: dict = None) -> str:
    """Generate What-How-Outcome variant.

    Emphasizes the WHO structure: What was done, How it was done, Outcome achieved.
    Best for product management and leadership roles.

    Args:
        text: Original bullet text
        client: LLM client with ask() method
        job_context: Optional job context for targeting

    Returns:
        Rewritten bullet in WHO format, or original text on failure
    """
    job_info = f" for {job_context.get('title', 'the target role')}" if job_context else ""

    prompt = (
        f"Rewrite this resume bullet using What-How-Outcome (WHO) format.\n\n"
        f"Structure:\n"
        f"- What: The achievement or deliverable\n"
        f"- How: The method, approach, or leadership applied\n"
        f"- Outcome: The business result or impact\n\n"
        f"Requirements:\n"
        f"- Keep to one concise line\n"
        f"- Emphasize scope and strategic impact\n"
        f"- Include stakeholder or cross-functional elements where relevant\n"
        f"- {_METRICS_PRESERVATION}\n\n"
        f"Original: {text}\n"
        f"Target Job{job_info}\n\n"
        f"Rewritten bullet:"
    )

    try:
        return client.ask(prompt, temperature=0.7)
    except Exception as exc:
        log.warning("WHO variant generation failed: %s. Returning original.", exc)
        return text
