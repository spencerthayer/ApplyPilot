"""Unified content guardrail — wraps statistical and semantic checks.

Provides a retry-aware wrapper that validates LLM output and retries
on failure with increasing strictness.
"""

from __future__ import annotations

import logging

from applypilot.guardrails.statistical_guard import GuardrailResult, check_token_retention
from applypilot.guardrails.semantic_guard import check_semantic
from applypilot.guardrails.thresholds import get_threshold

log = logging.getLogger(__name__)


class GuardrailEscalation(Exception):
    """Raised when all retry attempts fail guardrail validation."""


class ContentGuardrail:
    """Unified guardrail that validates LLM output against original content.

    Two modes:
      statistical: binomial proportion test for token retention (resume tailoring)
      semantic: factual claim extraction + profile verification (cover letters)
    """

    def __init__(self, context: str = "resume_tailoring"):
        config = get_threshold(context)
        self.context = context
        self.mode = config.mode
        self.threshold = config.threshold
        self.max_retries = config.max_retries

    def validate(self, original: str, output: str, profile: dict | None = None) -> GuardrailResult:
        match self.mode:
            case "statistical":
                return check_token_retention(original, output, p0=self.threshold)
            case "semantic" if profile is not None:
                return check_semantic(output, profile, max_fabrication_rate=self.threshold)
            case _:
                return GuardrailResult(passed=True, retention=1.0, threshold=self.threshold)

    def wrap(self, llm_func, original: str, *, profile: dict | None = None, **kwargs) -> str:
        """Call llm_func with retry on guardrail failure."""
        for attempt in range(self.max_retries):
            result = llm_func(original, **kwargs)
            check = self.validate(original, result, profile=profile)
            if check.passed:
                return result
            log.warning(
                "[guardrail:%s] Failed attempt %d/%d: %s",
                self.context,
                attempt + 1,
                self.max_retries,
                check.detail,
            )
            kwargs["strictness"] = attempt + 1
        raise GuardrailEscalation(f"[{self.context}] All {self.max_retries} attempts failed guardrail")
