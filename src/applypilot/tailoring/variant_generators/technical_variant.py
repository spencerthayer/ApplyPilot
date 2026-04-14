"""Technical Variant."""

import logging

log = logging.getLogger(__name__)

# Base instruction for all prompts to preserve metrics
_METRICS_PRESERVATION = (
    "Preserve all numbers and metrics exactly. Do not change, round, or fabricate any numerical values."
)


def generate_technical_variant(text: str, client, job_context: dict = None) -> str:
    """Generate technically-focused variant.

    Emphasizes specific technologies, architectures, algorithms, and technical depth.
    Best for engineering and technical roles.

    Args:
        text: Original bullet text
        client: LLM client with ask() method
        job_context: Optional job context for targeting

    Returns:
        Rewritten bullet emphasizing technical depth, or original text on failure
    """
    job_info = f" for {job_context.get('title', 'the target role')}" if job_context else ""

    prompt = (
        f"Rewrite this resume bullet to emphasize technical depth and implementation details.\n\n"
        f"Focus on:\n"
        f"- Specific technologies, frameworks, and tools used\n"
        f"- System architecture and design decisions\n"
        f"- Algorithms, data structures, or technical approaches\n"
        f"- Scale, performance, or infrastructure details\n\n"
        f"Requirements:\n"
        f"- Keep to one concise line\n"
        f"- Lead with the technical mechanism\n"
        f"- Include concrete technical artifacts (APIs, pipelines, models)\n"
        f"- {_METRICS_PRESERVATION}\n\n"
        f"Original: {text}\n"
        f"Target Job{job_info}\n\n"
        f"Rewritten bullet:"
    )

    try:
        return client.ask(prompt, temperature=0.7)
    except Exception as exc:
        log.warning("Technical variant generation failed: %s. Returning original.", exc)
        return text
