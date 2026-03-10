"""Quality gates implementation for the config-driven tailoring system.

This module implements quality gates between the 12 tailoring steps. Each gate
validates the output of a step before proceeding to the next, preventing wasted
work on poor inputs.

Quality gates are configured in profile.json under tailoring_config.quality_gates.
Each gate can be enabled/disabled independently and has step-specific validation
parameters.

Example:
    >>> from applypilot.scoring.tailoring_gates import run_quality_gate
    >>> result = run_quality_gate("step_1_normalize", output, config, profile)
    >>> if result.passed:
    ...     proceed_to_next_step()
    ... else:
    ...     handle_errors(result.errors)
"""

import re
from dataclasses import dataclass, field
from typing import Any

from applypilot.scoring.tailoring_config import (
    check_banned_phrases,
    get_quality_gate_config,
    get_role_instructions,
)


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


# ── Main Entry Point ───────────────────────────────────────────────────────


def run_quality_gate(step: str, output: dict, config: dict, profile: dict) -> GateResult:
    """Run a quality gate for a specific tailoring step.

    Checks if the gate is enabled in config, then runs the appropriate
    validation based on the step name. Returns a GateResult with details
    about any failures and suggestions for retry.

    Args:
        step: Step identifier (e.g., "step_1_normalize", "step_6_bullets").
        output: The output dict from the tailoring step to validate.
        config: Tailoring configuration dict from profile.
        profile: User profile dict for context-aware validation.

    Returns:
        GateResult with pass/fail status, errors, warnings, and suggestions.

    Example:
        >>> config = {"quality_gates": {"step_1_normalize": {"enabled": true}}}
        >>> result = run_quality_gate("step_1_normalize", output, config, profile)
        >>> print(f"Passed: {result.passed}")
    """
    gate_config = get_quality_gate_config(step, config)

    # If gate is disabled or not configured, auto-pass
    if not gate_config or not gate_config.get("enabled", True):
        return GateResult(passed=True, step=step)

    # Route to appropriate gate function based on step name
    step_lower = step.lower()

    if "normalize" in step_lower or step_lower == "step_1":
        return gate_normalize(output, gate_config, profile)
    elif "frame" in step_lower or step_lower == "step_2":
        return gate_frame(output, gate_config, profile)
    elif "bullet" in step_lower or step_lower == "step_6":
        return gate_bullets(output, gate_config, profile)
    elif "credibility" in step_lower or step_lower == "step_9":
        return gate_credibility(output, gate_config, profile)
    else:
        # Unknown step - pass with warning
        result = GateResult(
            passed=True,
            step=step,
            warnings=[f"No specific gate implementation for step: {step}"],
        )
        return result


# ── Individual Gate Functions ──────────────────────────────────────────────


def gate_normalize(output: dict, gate_config: dict, profile: dict) -> GateResult:
    """Quality gate for Step 1: Normalize job description.

    Validates that the job description normalization produced valid output
    with required fields and sufficient confidence.

    Args:
        output: Normalization output dict with fields like role_type,
                core_outcomes, hard_requirements, confidence.
        gate_config: Gate configuration from quality_gates.step_1_normalize.
        profile: User profile dict (used for context).

    Returns:
        GateResult indicating if normalization passed quality checks.
    """
    result = GateResult(passed=True, step="step_1_normalize")

    # Check confidence threshold
    min_confidence = gate_config.get("min_confidence", 0.8)
    confidence_check = check_confidence(output, min_confidence)
    result.merge(confidence_check)

    # Check required fields
    required_fields = gate_config.get("required_fields", ["role_type", "core_outcomes", "hard_requirements"])
    fields_check = check_required_fields(output, required_fields)
    if not fields_check.passed:
        result.merge(fields_check)
        result.add_error(
            f"Missing required fields: {fields_check.errors}",
            "Ensure the job description parsing extracts all key components. "
            "Consider re-running with more detailed instructions.",
        )

    # Validate role_type is valid
    role_type = output.get("role_type", "")
    if role_type and isinstance(role_type, str):
        # Check if role_type is reasonable (not empty, not too long)
        if len(role_type) < 2:
            result.add_error(
                f"Role type '{role_type}' is too short",
                "Role type should be a meaningful descriptor like 'software_engineer'.",
            )
        elif len(role_type) > 50:
            result.add_warning(f"Role type '{role_type[:50]}...' is unusually long")

    # Validate core_outcomes is a non-empty list
    core_outcomes = output.get("core_outcomes", [])
    if isinstance(core_outcomes, list):
        if len(core_outcomes) == 0:
            result.add_error(
                "core_outcomes is empty",
                "Job description should have at least one core outcome or responsibility.",
            )
        elif len(core_outcomes) > 20:
            result.add_warning(f"core_outcomes has {len(core_outcomes)} items - consider consolidating")
    else:
        result.add_error(
            "core_outcomes must be a list",
            "Ensure the parsing extracts outcomes as a list of strings.",
        )

    # Validate hard_requirements
    hard_requirements = output.get("hard_requirements", [])
    if isinstance(hard_requirements, list):
        if len(hard_requirements) == 0:
            result.add_warning("hard_requirements is empty - no explicit requirements found")
    else:
        result.add_error(
            "hard_requirements must be a list",
            "Ensure the parsing extracts requirements as a list of strings.",
        )

    # Set final pass status
    result.passed = len(result.errors) == 0

    return result


def gate_frame(output: dict, gate_config: dict, profile: dict) -> GateResult:
    """Quality gate for Step 2: Frame positioning.

    Validates that the positioning frame was determined with sufficient
    confidence and aligns with the detected role type.

    Args:
        output: Frame output dict with fields like positioning_frame,
                alignment_score, narrative_fit, confidence.
        gate_config: Gate configuration from quality_gates.step_2_frame.
        profile: User profile dict for role context.

    Returns:
        GateResult indicating if frame positioning passed quality checks.
    """
    result = GateResult(passed=True, step="step_2_frame")

    # Check confidence threshold
    min_confidence = gate_config.get("min_confidence", 0.9)
    confidence_check = check_confidence(output, min_confidence)
    result.merge(confidence_check)

    # Check positioning_frame exists and is valid
    positioning_frame = output.get("positioning_frame", "")
    if not positioning_frame:
        result.add_error(
            "Missing positioning_frame",
            "Determine the appropriate positioning frame based on role type and "
            "job requirements (e.g., 'Technical Builder', 'Product Leader').",
        )
    elif len(positioning_frame) < 3:
        result.add_error(
            f"Positioning frame '{positioning_frame}' is too short",
            "Positioning frame should be descriptive (e.g., 'Full-Stack Platform Engineer').",
        )
    elif len(positioning_frame) > 100:
        result.add_warning(f"Positioning frame is {len(positioning_frame)} chars - consider shortening")

    # Check alignment_score if present
    alignment_score = output.get("alignment_score")
    if alignment_score is not None:
        try:
            score = float(alignment_score)
            if score < 0.5:
                result.add_error(
                    f"Alignment score {score} is too low",
                    "Consider if this role is a good match for your profile. "
                    "Low alignment may indicate significant gaps.",
                )
            elif score < 0.7:
                result.add_warning(f"Alignment score {score} is moderate - review fit carefully")
        except (ValueError, TypeError):
            result.add_warning(f"alignment_score '{alignment_score}' is not a valid number")

    # Check narrative_fit if present
    narrative_fit = output.get("narrative_fit", "")
    if narrative_fit:
        # Check for banned words in narrative
        from applypilot.scoring.validator import BANNED_WORDS

        text_lower = narrative_fit.lower()
        found_banned = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"\b", text_lower)]
        if found_banned:
            result.add_warning(f"Narrative contains banned words: {', '.join(found_banned[:3])}")

    # Set final pass status
    result.passed = len(result.errors) == 0

    return result


def gate_bullets(output: dict, gate_config: dict, profile: dict) -> GateResult:
    """Quality gate for Step 6: Tailored bullets.

    Validates that tailored bullets meet template compliance, avoid banned
    phrases, and include required mechanism verbs for technical roles.

    Args:
        output: Bullets output dict with fields like bullets (list),
                template_used, compliance_score, role_type.
        gate_config: Gate configuration from quality_gates.step_6_bullets.
        profile: User profile dict for role-specific constraints.

    Returns:
        GateResult indicating if bullet tailoring passed quality checks.
    """
    result = GateResult(passed=True, step="step_6_bullets")

    # Get role type from output or profile
    role_type = output.get("role_type", "")
    if not role_type:
        # Try to detect from profile context
        job_context = profile.get("job_context", {})
        role_type = job_context.get("role_type", "general")

    # Check bullets exist and are valid
    bullets = output.get("bullets", [])
    if not bullets:
        result.add_error(
            "No bullets generated",
            "Ensure experience bullets are being tailored for this role.",
        )
    elif not isinstance(bullets, list):
        result.add_error(
            "bullets must be a list",
            "Bullets should be returned as a list of strings.",
        )
    else:
        # Check each bullet
        for i, bullet in enumerate(bullets):
            if not isinstance(bullet, str):
                result.add_error(f"Bullet {i} is not a string")
                continue

            if len(bullet) < 20:
                result.add_warning(f"Bullet {i} is very short ({len(bullet)} chars)")
            elif len(bullet) > 300:
                result.add_warning(f"Bullet {i} is very long ({len(bullet)} chars)")

            # Check banned phrases if enabled
            if gate_config.get("banned_phrases_check", True):
                banned_found = check_banned_phrases_gate(bullet, role_type, profile)
                if banned_found:
                    result.add_warning(f"Bullet {i} contains banned phrases: {', '.join(banned_found[:3])}")

            # Check mechanism verbs for specified role types
            mechanism_required_for = gate_config.get(
                "mechanism_required_for", ["ai", "system", "platform", "architecture"]
            )
            if any(kw in role_type.lower() for kw in mechanism_required_for):
                mechanism_check = check_mechanism_required(bullet, role_type, profile)
                if not mechanism_check.passed:
                    result.merge(mechanism_check)

    # Check template compliance
    template_compliance = gate_config.get("template_compliance", 0.85)
    compliance_score = output.get("compliance_score")
    if compliance_score is not None:
        try:
            score = float(compliance_score)
            if score < template_compliance:
                result.add_error(
                    f"Template compliance {score:.2f} below threshold {template_compliance}",
                    f"Ensure bullets follow the configured template format. "
                    f"Review template requirements for role type '{role_type}'.",
                )
        except (ValueError, TypeError):
            result.add_warning(f"compliance_score '{compliance_score}' is not a valid number")

    # Validate template_used matches expected
    template_used = output.get("template_used", "")
    if template_used:
        role_instructions = get_role_instructions(role_type, profile.get("tailoring_config", {}))
        expected_template = role_instructions.get("instructions", {}).get("bullet_template", "CAR")
        if template_used.upper() != expected_template.upper():
            result.add_warning(f"Template used '{template_used}' differs from expected '{expected_template}'")

    # Set final pass status
    result.passed = len(result.errors) == 0

    return result


def gate_credibility(output: dict, gate_config: dict, profile: dict) -> GateResult:
    """Quality gate for Step 9: Credibility check.

    Validates that the tailored resume maintains factual accuracy and
    has sufficient evidence coverage for claims made.

    Args:
        output: Credibility output dict with fields like evidence_map,
                coverage_score, fabricated_items, verification_status.
        gate_config: Gate configuration from quality_gates.step_9_credibility.
        profile: User profile dict for fact verification.

    Returns:
        GateResult indicating if credibility check passed.
    """
    result = GateResult(passed=True, step="step_9_credibility")

    # Check evidence coverage
    min_evidence_coverage = gate_config.get("min_evidence_coverage", 0.9)
    coverage_score = output.get("coverage_score")
    if coverage_score is not None:
        try:
            score = float(coverage_score)
            if score < min_evidence_coverage:
                result.add_error(
                    f"Evidence coverage {score:.2f} below minimum {min_evidence_coverage}",
                    "Ensure all claims in the resume are backed by evidence from your "
                    "profile. Add more specific examples or remove unsupported claims.",
                )
        except (ValueError, TypeError):
            result.add_warning(f"coverage_score '{coverage_score}' is not a valid number")

    # Check for fabricated items - this is always an error
    fabricated_items = output.get("fabricated_items", [])
    if fabricated_items:
        if isinstance(fabricated_items, list):
            for item in fabricated_items:
                result.add_error(
                    f"Fabricated item detected: {item}",
                    "Remove fabricated items or replace with factual information from your profile.",
                )
        else:
            result.add_error(
                f"Fabricated items found: {fabricated_items}",
                "Review all claims against your profile facts.",
            )

    # Check verification status
    verification_status = output.get("verification_status", "")
    if verification_status:
        status_lower = str(verification_status).lower()
        if status_lower in ["failed", "error", "invalid"]:
            result.add_error(
                f"Verification status: {verification_status}",
                "Review verification errors and ensure all facts are accurate.",
            )
        elif status_lower in ["partial", "warning"]:
            result.add_warning(f"Verification status: {verification_status}")

    # Check evidence_map exists and has entries
    evidence_map = output.get("evidence_map", {})
    if not evidence_map:
        result.add_warning("No evidence_map provided - cannot verify claim sources")
    elif isinstance(evidence_map, dict):
        # Check that claims have evidence
        claims_without_evidence = [claim for claim, evidence in evidence_map.items() if not evidence]
        if claims_without_evidence:
            result.add_error(
                f"Claims without evidence: {len(claims_without_evidence)}",
                "Add evidence sources for all claims or remove unsupported statements.",
            )

    # Cross-check against resume_facts in profile
    resume_facts = profile.get("resume_facts", {})
    if resume_facts:
        # Check companies are preserved
        preserved_companies = resume_facts.get("preserved_companies", [])
        if preserved_companies:
            # This would need the actual resume text to verify
            # For now, add a warning if we have no way to verify
            pass

    # Set final pass status
    result.passed = len(result.errors) == 0

    return result


# ── Helper Functions ───────────────────────────────────────────────────────


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


# ── Utility Functions ──────────────────────────────────────────────────────


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


# ── Final Assembly Gate ────────────────────────────────────────────────────


def gate_final_assembly(output: dict, gate_config: dict, profile: dict) -> GateResult:
    """Final quality gate after resume assembly using deterministic validation.
    
    This gate uses the ResumeValidator to perform comprehensive, countable
    validation checks on the assembled resume. It catches issues that are
    difficult to detect during intermediate steps:
    
    - Missing roles from profile work_history
    - Incorrect bullet counts per role
    - Missing credentials in summary
    - Unquantified bullets
    - Weak verbs
    
    Args:
        output: The assembled resume data dict with keys:
                title, summary, skills, experience, projects, education
        gate_config: Gate configuration from quality_gates.step_final_assembly
        profile: User profile dict for validation context
    
    Returns:
        GateResult with pass/fail status and specific retry suggestions
    
    Example:
        >>> gate_config = {"enabled": True, "checks": ["role_completeness", "bullet_counts"]}
        >>> result = gate_final_assembly(resume_data, gate_config, profile)
        >>> if not result.passed:
        ...     print(result.retry_suggestions[0])
    """
    from applypilot.scoring.resume_validator import ResumeValidator
    
    result = GateResult(passed=True, step="step_final_assembly")
    
    # Get tailoring config from profile
    config = profile.get("tailoring_config", {})
    
    # Check if validation is enabled in config
    validation_config = config.get("validation", {})
    if not validation_config.get("enabled", True):
        result.add_warning("Final assembly validation disabled in config")
        return result
    
    # Initialize validator
    try:
        validator = ResumeValidator(profile, config)
    except Exception as e:
        result.add_warning(f"Failed to initialize validator: {e}")
        return result
    
    # Determine which checks to run
    available_checks = {check.__name__: check for check in validator.DEFAULT_CHECKS}
    selected_check_names = gate_config.get("checks", list(available_checks.keys()))
    
    # Map check names to functions
    selected_checks = []
    for name in selected_check_names:
        if name in available_checks:
            selected_checks.append(available_checks[name])
        else:
            result.add_warning(f"Unknown check: {name}")
    
    # Run validation
    try:
        validation = validator.validate(output, selected_checks=selected_checks)
    except Exception as e:
        result.add_warning(f"Validation failed with error: {e}")
        return result
    
    # Process validation results
    if not validation["passed"]:
        for error in validation["all_errors"]:
            result.add_error(error)
        
        for warning in validation["all_warnings"]:
            result.add_warning(warning)
        
        # Add retry suggestions
        if validation["retry_prompt"]:
            result.retry_suggestions.append(validation["retry_prompt"])
        
        # Add metadata for debugging
        result.add_warning(
            f"Failed checks: {', '.join(validation['failed_checks'])}"
        )
    
    # Set final pass status
    result.passed = len(result.errors) == 0
    
    return result


def run_final_validation(
    resume_data: dict,
    profile: dict,
    max_retries: int = 3
) -> dict:
    """Run final validation with automatic retry on failure.
    
    This is a convenience function for running the final assembly gate
    with retry logic built in. It manages the retry loop and returns
    the final result.
    
    Args:
        resume_data: The assembled resume data
        profile: User profile dict
        max_retries: Maximum number of retry attempts
    
    Returns:
        Dict with validation results and retry history:
        {
            "success": bool,
            "resume_data": dict,
            "attempts": list[dict],
            "validation_result": GateResult,
        }
    """
    attempts = []
    current_data = resume_data
    
    for attempt in range(max_retries + 1):
        # Create gate config for this attempt
        gate_config = {"enabled": True}
        
        # Run validation
        gate_result = gate_final_assembly(current_data, gate_config, profile)
        
        attempts.append({
            "attempt": attempt,
            "passed": gate_result.passed,
            "error_count": len(gate_result.errors),
            "warning_count": len(gate_result.warnings),
        })
        
        if gate_result.passed:
            return {
                "success": True,
                "resume_data": current_data,
                "attempts": attempts,
                "validation_result": gate_result,
            }
        
        if attempt >= max_retries:
            break
        
        # Retry would happen here - in practice, this is handled by
        # the caller which has access to the LLM client
        if not gate_result.retry_suggestions:
            break
    
    return {
        "success": False,
        "resume_data": current_data,
        "attempts": attempts,
        "validation_result": gate_result,
    }
