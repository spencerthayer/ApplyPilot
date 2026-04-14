"""Car Variant."""

import logging

log = logging.getLogger(__name__)

# Base instruction for all prompts to preserve metrics
_METRICS_PRESERVATION = (
    "Preserve all numbers and metrics exactly. Do not change, round, or fabricate any numerical values."
)


def generate_car_variant(text: str, client, job_context: dict = None) -> str:
    """Generate Challenge-Action-Result variant.

    Emphasizes the CAR structure: Context/Challenge, Action taken, Result achieved.
    Best for demonstrating problem-solving and measurable outcomes.

    Args:
        text: Original bullet text
        client: LLM client with ask() method
        job_context: Optional job context for targeting

    Returns:
        Rewritten bullet in CAR format, or original text on failure
    """
    job_info = f" for {job_context.get('title', 'the target role')}" if job_context else ""

    prompt = (
        f"Rewrite this resume bullet using Challenge-Action-Result (CAR) format.\n\n"
        f"Structure:\n"
        f"- Challenge: The problem or situation faced\n"
        f"- Action: What you specifically did (include technologies/mechanisms)\n"
        f"- Result: The measurable outcome\n\n"
        f"Requirements:\n"
        f"- Keep to one concise line\n"
        f"- Start with a strong action verb\n"
        f"- Include specific technologies where relevant\n"
        f"- {_METRICS_PRESERVATION}\n\n"
        f"Original: {text}\n"
        f"Target Job{job_info}\n\n"
        f"Rewritten bullet:"
    )

    try:
        return client.ask(prompt, temperature=0.7)
    except Exception as exc:
        log.warning("CAR variant generation failed: %s. Returning original.", exc)
        return text
