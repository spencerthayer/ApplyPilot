"""Product Variant."""

import logging

log = logging.getLogger(__name__)

# Base instruction for all prompts to preserve metrics
_METRICS_PRESERVATION = (
    "Preserve all numbers and metrics exactly. Do not change, round, or fabricate any numerical values."
)


def generate_product_variant(text: str, client, job_context: dict = None) -> str:
    """Generate product-impact-focused variant.

    Emphasizes business metrics, user impact, revenue/growth, and strategic outcomes.
    Best for product management and business-focused roles.

    Args:
        text: Original bullet text
        client: LLM client with ask() method
        job_context: Optional job context for targeting

    Returns:
        Rewritten bullet emphasizing product impact, or original text on failure
    """
    job_info = f" for {job_context.get('title', 'the target role')}" if job_context else ""

    prompt = (
        f"Rewrite this resume bullet to emphasize product impact and business outcomes.\n\n"
        f"Focus on:\n"
        f"- Business metrics (revenue, growth, efficiency)\n"
        f"- User impact and customer outcomes\n"
        f"- Strategic importance and scope\n"
        f"- Cross-functional leadership and stakeholder alignment\n\n"
        f"Requirements:\n"
        f"- Keep to one concise line\n"
        f"- Lead with the business outcome or user impact\n"
        f"- Quantify results where possible\n"
        f"- {_METRICS_PRESERVATION}\n\n"
        f"Original: {text}\n"
        f"Target Job{job_info}\n\n"
        f"Rewritten bullet:"
    )

    try:
        return client.ask(prompt, temperature=0.7)
    except Exception as exc:
        log.warning("Product variant generation failed: %s. Returning original.", exc)
        return text
