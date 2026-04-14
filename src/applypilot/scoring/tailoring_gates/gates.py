"""Quality gate dispatcher."""

"""Gates for tailoring quality gates."""

from applypilot.scoring.tailoring_gates.models import GateResult
from applypilot.scoring.tailoring_config import (
    get_quality_gate_config,
)

# ── Gates ──────────────────────────────────────────────────────────────

from applypilot.scoring.tailoring_gates.individual_gates import (
    gate_normalize,
    gate_frame,
    gate_bullets,
    gate_credibility,
)


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
