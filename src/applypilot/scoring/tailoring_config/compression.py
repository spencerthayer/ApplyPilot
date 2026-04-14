"""Compression."""

"""Config-driven tailoring configuration module.

Loads and validates tailoring configuration from the normalized profile contract,
detects role types from job titles, loads example resumes, and provides
role-specific instructions, constraints, and guidelines.

All configuration is explicit - no magic folders or implicit behavior.
"""

import re

from applypilot.scoring.tailoring_config.rules import get_global_rules

# Default configuration values
DEFAULT_ROLE_TYPE = "general"
DEFAULT_BULLET_TEMPLATE = "CAR"
DEFAULT_MAX_BULLETS_PER_ROLE = 6
DEFAULT_MAX_SUMMARY_LINES = 4
DEFAULT_SELECTED_IMPACT_METRICS = 5


def should_compress_role(years_ago: int, config: dict) -> bool:
    """Determine if a role should be compressed based on age.

    Args:
        years_ago: Number of years since the role.
        config: Tailoring configuration dict.

    Returns:
        True if role should be compressed, False otherwise.
    """
    global_rules = get_global_rules(config)
    compression = global_rules.get("role_compression", {})

    if not compression.get("enabled", True):
        return False

    threshold = compression.get("older_than_years", 10)
    return years_ago >= threshold


def get_max_bullets_for_role(years_ago: int, role_type: str, config: dict) -> int:
    """Get maximum bullets allowed for a role based on age and config.

    Args:
        years_ago: Number of years since the role.
        role_type: Role type key.
        config: Tailoring configuration dict.

    Returns:
        Maximum number of bullets allowed.
    """
    # Get role-specific max
    role_config = config.get("role_types", {}).get(role_type, {})
    role_instructions = role_config.get("instructions", {})
    default_max = role_instructions.get("max_bullets_per_role", DEFAULT_MAX_BULLETS_PER_ROLE)

    # Check if compression applies
    if should_compress_role(years_ago, config):
        global_rules = get_global_rules(config)
        compression = global_rules.get("role_compression", {})
        return compression.get("max_bullets_per_old_role", 3)

    return default_max


def get_quality_gate_config(step: str | int, config: dict) -> dict:
    """Get quality gate configuration for a specific step.

    Args:
        step: Step name or number (e.g., "step_1_normalize" or 1).
        config: Tailoring configuration dict.

    Returns:
        Quality gate configuration dict, or empty dict if not found/disabled.
    """
    quality_gates = config.get("quality_gates", {})

    # Normalize step name
    if isinstance(step, int):
        step_name = f"step_{step}_normalize"
        # Try common suffixes
        for suffix in [
            "normalize",
            "frame",
            "extract",
            "select",
            "tailor",
            "bullets",
            "format",
            "review",
            "credibility",
        ]:
            candidate = f"step_{step}_{suffix}"
            if candidate in quality_gates:
                step_name = candidate
                break
    else:
        step_name = step

    gate_config = quality_gates.get(step_name, {})

    if not gate_config.get("enabled", True):
        return {}

    return gate_config


def get_merge_config(config: dict) -> dict:
    """Get role merge configuration with defaults applied.

    This configuration controls how roles are merged in the tailored resume:
    - Recent roles (last 10 years by default) are never merged
    - Older roles can be merged if needed for space
    - Per-role overrides can force separate or allow merge

    Args:
        config: Tailoring configuration dict.

    Returns:
        Merge configuration dict with keys:
            - recent_cutoff_year: Year threshold for recent vs older roles
            - max_bullets_recent: Maximum bullets for recent roles
            - max_bullets_merged: Maximum bullets for merged older roles
            - per_role_overrides: Dict of role patterns to merge settings
    """
    global_rules = config.get("global_rules", {})
    merge_config = global_rules.get("role_merge", {})

    return {
        "recent_cutoff_year": merge_config.get("recent_cutoff_year", 2015),
        "max_bullets_recent": merge_config.get("max_bullets_recent", 5),
        "max_bullets_merged": merge_config.get("max_bullets_merged", 3),
        "per_role_overrides": merge_config.get("per_role_overrides", {}),
    }


def should_merge_role(role_title: str, role_company: str, role_dates: str, config: dict) -> bool:
    """Determine if a specific role should be merged based on configuration.

    Checks per-role overrides first, then falls back to date-based rules.

    Args:
        role_title: The role title/position.
        role_company: The company name.
        role_dates: The date string for the role.
        config: Tailoring configuration dict.

    Returns:
        True if role should be merged, False otherwise.
    """
    merge_config = get_merge_config(config)
    overrides = merge_config.get("per_role_overrides", {})

    # Check for per-role overrides first
    role_key = f"{role_company}|{role_title}".lower()
    for pattern, setting in overrides.items():
        if pattern.lower() in role_key or pattern.lower() in role_title.lower():
            return setting.get("allow_merge", False)

    # Fall back to date-based rules
    # Extract year from dates and compare to cutoff
    year_match = re.search(r"(\d{4})", role_dates)
    if year_match:
        year = int(year_match.group(1))
        return year < merge_config["recent_cutoff_year"]

    # Default to not merging if we can't determine
    return False
