"""Guardrail thresholds — reads from RuntimeConfig when available, falls back to defaults.

Every threshold is user-configurable via config.yaml at 3 precedence levels:
  API param > CLI flag > config.yaml > hardcoded default (LLD §16.1).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GuardrailThreshold:
    mode: str  # "statistical" or "semantic"
    threshold: float
    max_retries: int = 3


# Static defaults — used when RuntimeConfig isn't available yet
_DEFAULTS: dict[str, GuardrailThreshold] = {
    "resume_tailoring": GuardrailThreshold("statistical", 0.40),
    "track_resume": GuardrailThreshold("statistical", 0.60),
    "variant_generation": GuardrailThreshold("statistical", 0.70),
    "combo_blending": GuardrailThreshold("statistical", 0.50),
    "cover_letter": GuardrailThreshold("semantic", 0.15),
    "profile_enrichment": GuardrailThreshold("semantic", 0.0),
}


def get_threshold(context: str) -> GuardrailThreshold:
    """Get threshold for a context, preferring RuntimeConfig if available."""
    try:
        from applypilot.bootstrap import get_app

        tc = get_app().config.tailoring
        match context:
            case "resume_tailoring":
                return GuardrailThreshold("statistical", tc.retention_threshold)
            case "track_resume":
                return GuardrailThreshold("statistical", tc.track_retention_threshold)
            case "variant_generation":
                return GuardrailThreshold("statistical", tc.variant_retention_threshold)
            case "combo_blending":
                return GuardrailThreshold("statistical", tc.combo_retention_threshold)
            case "cover_letter":
                return GuardrailThreshold("semantic", tc.cover_letter_fabrication_threshold)
            case "profile_enrichment":
                return GuardrailThreshold("semantic", tc.profile_enrichment_fabrication_threshold)
    except Exception:
        pass
    return _DEFAULTS.get(context, _DEFAULTS["resume_tailoring"])


# Backward compat
THRESHOLDS = _DEFAULTS
