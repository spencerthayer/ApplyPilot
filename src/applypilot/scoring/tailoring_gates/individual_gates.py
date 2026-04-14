"""Individual quality gate implementations."""

"""Gates for tailoring quality gates."""

import re

from applypilot.scoring.tailoring_gates.models import GateResult
from applypilot.scoring.tailoring_gates.helpers import (
    check_confidence,
    check_required_fields,
    check_banned_phrases_gate,
    check_mechanism_required,
)
from applypilot.scoring.tailoring_config import (
    get_role_instructions,
)


# ── Gates ──────────────────────────────────────────────────────────────


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

    # Set final pass status
    result.passed = len(result.errors) == 0

    return result


def gate_final_assembly(output: dict, gate_config: dict, profile: dict) -> GateResult:
    """Final quality gate after resume assembly using deterministic validation.

    This gate uses the ResumeValidator to perform comprehensive, countable
    validation checks on the assembled resume. It catches issues that are
    difficult to detect during intermediate steps:

    - Missing roles from profile work entries
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
        result.add_warning(f"Failed checks: {', '.join(validation['failed_checks'])}")

    # Set final pass status
    result.passed = len(result.errors) == 0

    return result
