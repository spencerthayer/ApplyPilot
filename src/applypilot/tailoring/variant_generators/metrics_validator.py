"""Metrics Validator."""

import logging
import re

log = logging.getLogger(__name__)

# Base instruction for all prompts to preserve metrics
_METRICS_PRESERVATION = (
    "Preserve all numbers and metrics exactly. Do not change, round, or fabricate any numerical values."
)


def _extract_numbers(text: str) -> set[str]:
    """Extract all numeric values from text as strings."""
    # Match integers, decimals, percentages, currency
    pattern = r"\d+(?:\.\d+)?(?:%|\s*(?:USD|EUR|GBP|\$|€|£))?"
    return set(re.findall(pattern, text))


def validate_variant_metrics(original: str, variant: str, registry: dict) -> str:
    """Validate that variant metrics are verified against registry.

    Checks if the variant contains any metrics not present in the original
    or not verified in the registry. Returns original text if hallucinated
    metrics are detected.

    Args:
        original: Original bullet text with verified metrics
        variant: LLM-generated variant to validate
        registry: MetricsRegistry or dict mapping metric strings to verification status

    Returns:
        Variant if all metrics are verified, otherwise original text
    """
    # Extract numbers from both texts
    original_nums = _extract_numbers(original)
    variant_nums = _extract_numbers(variant)

    # Check for new numbers in variant not in original
    new_numbers = variant_nums - original_nums

    if not new_numbers:
        # No new numbers, variant is safe
        return variant

    # Check if new numbers are in registry
    if registry:
        # Handle both MetricsRegistry objects and dicts
        if hasattr(registry, "is_verified"):
            # MetricsRegistry object
            for num in new_numbers:
                if not registry.is_verified(num):
                    log.warning("Rejecting variant with unverified metric '%s' not in registry", num)
                    return original
        elif isinstance(registry, dict):
            # Dict of verified metrics
            for num in new_numbers:
                if num not in registry:
                    log.warning("Rejecting variant with unverified metric '%s' not in registry", num)
                    return original
    else:
        # No registry provided, reject if any new numbers
        log.warning("Rejecting variant with new metrics (no registry): %s", new_numbers)
        return original

    # All new numbers are verified
    return variant
