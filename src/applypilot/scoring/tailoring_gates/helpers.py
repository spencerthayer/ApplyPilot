"""Helpers for tailoring quality gates."""

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

import re
from typing import Any

from applypilot.scoring.tailoring_gates.models import GateResult
from applypilot.scoring.tailoring_config import (
    check_banned_phrases,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def check_confidence(output: dict, min_confidence: float) -> GateResult:
    """Check if output confidence meets minimum threshold.

    Args:
        output: Output dict that may contain a 'confidence' key.
        min_confidence: Minimum acceptable confidence (0.0-1.0).

    Returns:
        GateResult with pass/fail based on confidence check.
    """
    result = GateResult(passed=True)

    confidence = output.get("confidence")
    if confidence is None:
        result.add_warning("No confidence score provided in output")
        return result

    try:
        score = float(confidence)
        result.confidence = score

        if score < 0 or score > 1:
            result.add_error(
                f"Confidence score {score} is out of range (0.0-1.0)",
                "Confidence must be a value between 0.0 and 1.0.",
            )
        elif score < min_confidence:
            result.add_error(
                f"Confidence {score:.2f} below minimum {min_confidence}",
                "Review the output for accuracy. Low confidence may indicate ambiguous input or unclear requirements.",
            )
    except (ValueError, TypeError):
        result.add_error(
            f"Invalid confidence value: {confidence}",
            "Confidence must be a numeric value between 0.0 and 1.0.",
        )

    result.passed = len(result.errors) == 0
    return result


def check_required_fields(output: dict, required_fields: list[str]) -> GateResult:
    """Check if all required fields exist in output.

    Args:
        output: Output dict to validate.
        required_fields: List of field names that must exist.

    Returns:
        GateResult with pass/fail based on field presence.
    """
    result = GateResult(passed=True)

    missing = []
    for field_name in required_fields:
        # Treat missing keys or falsy values (empty string, empty list/dict, None, 0) as missing
        if not output.get(field_name):
            # Distinguish between completely missing and present-but-empty
            if field_name not in output:
                missing.append(field_name)
            else:
                missing.append(f"{field_name} (empty)")

    if missing:
        result.errors = [f"Missing required field: {f}" for f in missing]
        result.passed = False

    return result


def check_banned_phrases_gate(text: str, role_type: str, profile: dict) -> list[str]:
    """Check text for banned phrases specific to a role type.

    This is a gate-specific wrapper around the tailoring_config function
    that also checks global banned words.

    Args:
        text: Text to check for banned phrases.
        role_type: Role type key for role-specific banned phrases.
        profile: User profile dict containing tailoring_config.

    Returns:
        List of banned phrases found in text.
    """
    found: list[str] = []

    # Check role-specific banned phrases
    config = profile.get("tailoring_config", {})
    role_found = check_banned_phrases(text, role_type, config)
    found.extend(role_found)

    # Also check global banned words from validator
    from applypilot.scoring.validator import BANNED_WORDS

    text_lower = text.lower()
    for word in BANNED_WORDS:
        if re.search(r"\b" + re.escape(word) + r"\b", text_lower):
            if word not in found:
                found.append(word)

    return found


def check_mechanism_required(text: str, role_type: str, profile: dict) -> GateResult:
    """Check if text contains mechanism verbs (for technical roles).

    Technical roles often require mechanism verbs like 'built', 'designed',
    'implemented', 'architected' to demonstrate technical action.

    Args:
        text: Text to check (typically a bullet point).
        role_type: Role type key for context.
        profile: User profile dict containing tailoring_config.

    Returns:
        GateResult indicating if mechanism verbs are present.
    """
    result = GateResult(passed=True)

    # Default mechanism verbs
    default_mechanisms = [
        "built",
        "designed",
        "implemented",
        "architected",
        "developed",
        "created",
        "engineered",
        "constructed",
        "deployed",
        "delivered",
    ]

    # Get role-specific mechanism verbs if configured
    config = profile.get("tailoring_config", {})
    role_config = config.get("role_types", {}).get(role_type, {})
    constraints = role_config.get("constraints", {})

    # Check if mechanism is required for this role
    mechanism_required = constraints.get("mechanism_required", True)
    if not mechanism_required:
        return result  # Mechanism not required, auto-pass

    # Get required patterns or use defaults
    required_patterns = constraints.get("required_patterns", default_mechanisms)
    if not required_patterns:
        required_patterns = default_mechanisms

    # Check for at least one mechanism verb using word-boundary regex to avoid substrings
    text_lower = text.lower()
    pattern = r"\b(" + "|".join(map(re.escape, required_patterns)) + r")\b"
    found_mechanisms = re.findall(pattern, text_lower)

    if not found_mechanisms:
        result.add_error(
            f"Missing mechanism verb. Expected one of: {', '.join(required_patterns[:5])}...",
            "Add a technical action verb that shows what you built or how you built it. "
            "Examples: 'Built API serving 1M requests/day', "
            "'Architected data pipeline processing 10TB daily'.",
        )
        result.passed = False

    return result


def check_template_compliance(text: str, template: str, threshold: float) -> GateResult:
    """Check if text complies with a specified template format.

    Currently supports CAR (Challenge-Action-Result) and WHO (What-How-Outcome)
    templates. Future versions may support additional formats.

    Args:
        text: Text to check (typically a bullet point).
        template: Template name (e.g., "CAR", "WHO").
        threshold: Minimum compliance score (0.0-1.0).

    Returns:
        GateResult indicating template compliance.
    """
    result = GateResult(passed=True)

    template_upper = template.upper()
    text_lower = text.lower()

    if template_upper == "CAR":
        # CAR: Challenge/Context, Action, Result
        # Look for action verbs and result indicators
        action_indicators = [
            "built",
            "designed",
            "implemented",
            "created",
            "developed",
            "led",
            "managed",
            "architected",
        ]
        result_indicators = [
            "resulting",
            "resulted",
            "leading to",
            "reducing",
            "improving",
            "increasing",
            "decreasing",
            "by",
            "%",
            "x",
            "times",
            "fold",
        ]

        has_action = any(a in text_lower for a in action_indicators)
        has_result = any(r in text_lower for r in result_indicators)

        score = 0.0
        if has_action:
            score += 0.5
        if has_result:
            score += 0.5

        if score < threshold:
            missing = []
            if not has_action:
                missing.append("action verb")
            if not has_result:
                missing.append("result/metric")
            result.add_error(
                f"CAR template compliance {score:.2f} below threshold {threshold}",
                f"Add missing elements: {', '.join(missing)}. "
                f"Format: [Action] + [System/Method] + [Measurable Result].",
            )
            result.passed = False

    elif template_upper == "WHO":
        # WHO: What, How, Outcome
        # Look for scope indicators and outcome
        scope_indicators = [
            "led",
            "drove",
            "delivered",
            "managed",
            "owned",
            "responsible for",
        ]
        outcome_indicators = [
            "achieved",
            "delivered",
            "resulting",
            "enabling",
            "driving",
        ]

        has_scope = any(s in text_lower for s in scope_indicators)
        has_outcome = any(o in text_lower for o in outcome_indicators)

        score = 0.0
        if has_scope:
            score += 0.5
        if has_outcome:
            score += 0.5

        if score < threshold:
            missing = []
            if not has_scope:
                missing.append("scope/ownership")
            if not has_outcome:
                missing.append("business outcome")
            result.add_error(
                f"WHO template compliance {score:.2f} below threshold {threshold}",
                f"Add missing elements: {', '.join(missing)}. Format: [Action] + [Scope/Decision] + [Business Result].",
            )
            result.passed = False

    else:
        # Unknown template - pass with warning
        result.add_warning(f"Unknown template type: {template}")

    return result


def get_gate_status(
        gate_results: list[GateResult],
) -> dict[str, Any]:
    """Get aggregate status from multiple gate results.

    Args:
        gate_results: List of GateResult objects from various steps.

    Returns:
        Dict with overall status, error count, warning count, etc.
    """
    total = len(gate_results)
    passed = sum(1 for g in gate_results if g.passed)
    failed = total - passed

    all_errors: list[str] = []
    all_warnings: list[str] = []
    all_suggestions: list[str] = []

    for result in gate_results:
        all_errors.extend([f"[{result.step}] {e}" for e in result.errors])
        all_warnings.extend([f"[{result.step}] {w}" for w in result.warnings])
        all_suggestions.extend(result.retry_suggestions)

    return {
        "total_gates": total,
        "passed": passed,
        "failed": failed,
        "overall_passed": failed == 0,
        "total_errors": len(all_errors),
        "total_warnings": len(all_warnings),
        "errors": all_errors,
        "warnings": all_warnings,
        "retry_suggestions": list(set(all_suggestions)),  # Deduplicate
    }


def should_retry(gate_result: GateResult, attempts: int = 0, max_retries: int = 3) -> bool:
    """Determine if a failed gate should trigger a retry.

    Args:
        gate_result: The GateResult to evaluate.
        max_retries: Maximum number of retry attempts allowed.

    Returns:
        True if retry is recommended, False otherwise.
    """
    if gate_result.passed:
        return False

    # Respect max retry attempts
    if attempts >= max_retries:
        return False

    # Don't retry if no suggestions available
    if not gate_result.retry_suggestions:
        return False

    # Check error types - some errors are not retryable
    non_retryable = [
        "missing required field",
        "fabricated",
        "out of range",
    ]

    for error in gate_result.errors:
        error_lower = error.lower()
        # If any non-retryable error is present, do not retry
        if any(nr in error_lower for nr in non_retryable):
            return False

    # If we have suggestions and no non-retryable errors, retry is possible
    return bool(gate_result.retry_suggestions)
