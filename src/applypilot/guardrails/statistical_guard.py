"""Statistical guardrail — binomial proportion test for token retention.

Extracts the existing deviation guard logic from scoring/validator.py
into a standalone, reusable guardrail class.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass
class GuardrailResult:
    passed: bool
    retention: float
    threshold: float
    detail: str = ""


def check_token_retention(
        original: str,
        output: str,
        p0: float = 0.40,
        alpha: float = 0.01,
) -> GuardrailResult:
    """Binomial proportion test: does the tailored output retain enough original tokens?

    Args:
        original: Original resume text.
        output: Tailored/modified resume text.
        p0: Expected baseline retention (default 40%).
        alpha: Significance level (default 1%).

    Returns:
        GuardrailResult with pass/fail and retention stats.
    """
    orig_tokens = _tokenize(original)
    out_tokens = _tokenize(output)

    if not orig_tokens:
        return GuardrailResult(passed=True, retention=1.0, threshold=p0, detail="empty original")

    retained = orig_tokens & out_tokens
    retention = len(retained) / len(orig_tokens)

    # Binomial threshold: p0 + z * SE
    z = _z_score(alpha)
    se = math.sqrt(p0 * (1 - p0) / len(orig_tokens))
    threshold = p0 + z * se

    passed = retention >= threshold
    return GuardrailResult(
        passed=passed,
        retention=retention,
        threshold=threshold,
        detail=f"retained {len(retained)}/{len(orig_tokens)} tokens ({retention:.2%}), threshold={threshold:.2%}",
    )


def _tokenize(text: str) -> set[str]:
    """Lowercase word tokenization, filtering stopwords and short tokens."""
    words = re.findall(r"[a-z0-9]+(?:[-'][a-z0-9]+)*", text.lower())
    return {w for w in words if len(w) > 2}


def _z_score(alpha: float) -> float:
    """Approximate z-score for one-sided test."""
    # Common values
    if alpha <= 0.01:
        return -2.326
    if alpha <= 0.05:
        return -1.645
    return -1.282
