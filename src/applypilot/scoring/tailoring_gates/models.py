"""Models for tailoring quality gates."""

"""Quality gates implementation for the config-driven tailoring system.

This module implements quality gates between the 12 tailoring steps. Each gate
validates the output of a step before proceeding to the next, preventing wasted
work on poor inputs.

Quality gates are configured on the normalized profile contract under
tailoring_config.quality_gates. Each gate can be enabled/disabled
independently and has step-specific validation parameters.

Example:
    >>> from applypilot.scoring.tailoring_gates import run_quality_gate
    >>> result = run_quality_gate("step_1_normalize", output, config, profile)
    >>> if result.passed:
    ...     proceed_to_next_step()
    ... else:
    ...     handle_errors(result.errors)
"""

from dataclasses import dataclass, field


# ── Data Classes ───────────────────────────────────────────────────────────


@dataclass
class GateResult:
    """Result of a quality gate check.

    Attributes:
        passed: Whether the gate check passed (True) or failed (False).
        step: The step name that was checked (e.g., "step_1_normalize").
        errors: List of error messages for failures that block progress.
        warnings: List of warning messages that don't block but should be noted.
        confidence: Confidence score from 0.0 to 1.0 (if applicable).
        retry_suggestions: List of suggestions for fixing failures.
    """

    passed: bool
    step: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.0
    retry_suggestions: list[str] = field(default_factory=list)

    def add_error(self, message: str, suggestion: str | None = None) -> None:
        """Add an error and optionally a retry suggestion."""
        self.errors.append(message)
        if suggestion:
            self.retry_suggestions.append(suggestion)

    def add_warning(self, message: str) -> None:
        """Add a warning message."""
        self.warnings.append(message)

    def merge(self, other: "GateResult") -> "GateResult":
        """Merge another GateResult into this one."""
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.retry_suggestions.extend(other.retry_suggestions)
        self.passed = self.passed and other.passed
        if other.confidence > 0:
            self.confidence = other.confidence
        return self
