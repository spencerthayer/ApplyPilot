"""Tailoring config — re-exports."""

from applypilot.scoring.tailoring_config.loader import load_tailoring_config, validate_tailoring_config, load_examples
from applypilot.scoring.tailoring_config.role_detection import (
    detect_role_type,
    get_role_instructions,
    list_role_types,
    get_role_detection_keywords,
)
from applypilot.scoring.tailoring_config.rules import (
    apply_global_rules,
    get_global_rules,
    _normalize_dates_to_yyyy_mm,
    _normalize_bullet_case,
    check_banned_phrases,
    check_required_patterns,
)
from applypilot.scoring.tailoring_config.compression import (
    should_compress_role,
    get_max_bullets_for_role,
    get_quality_gate_config,
    get_merge_config,
    should_merge_role,
)

__all__ = [
    "load_tailoring_config",
    "validate_tailoring_config",
    "load_examples",
    "detect_role_type",
    "get_role_instructions",
    "list_role_types",
    "get_role_detection_keywords",
    "apply_global_rules",
    "get_global_rules",
    "_normalize_dates_to_yyyy_mm",
    "_normalize_bullet_case",
    "check_banned_phrases",
    "check_required_patterns",
    "should_compress_role",
    "get_max_bullets_for_role",
    "get_quality_gate_config",
    "get_merge_config",
    "should_merge_role",
]
