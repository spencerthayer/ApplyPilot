"""Resume deviation guard — binomial proportion test for token retention."""

from __future__ import annotations

import math

from applypilot.scoring.validator.sanitizer import tokenize_words


def check_resume_deviation(original: str, tailored: str, alpha: float = 0.01) -> tuple[bool, float]:
    """Check if a tailored resume deviates too far from the original.

    Uses a binomial proportion test: given N original tokens, we expect at
    least a baseline fraction to survive in the tailored version.

    Returns:
        (passed, retention_rate) — passed is False if deviation is anomalous.
    """
    if not original or not tailored:
        return True, 1.0

    orig_tokens = tokenize_words(original)
    tail_tokens = tokenize_words(tailored)

    if not orig_tokens:
        return True, 1.0

    n = len(orig_tokens)
    retained = len(orig_tokens & tail_tokens)
    p_hat = retained / n

    p0 = 0.40
    se = math.sqrt(p0 * (1 - p0) / n)
    z_crit = -2.326 if alpha <= 0.01 else -1.645
    threshold = p0 + z_crit * se

    return p_hat >= threshold, round(p_hat, 3)
