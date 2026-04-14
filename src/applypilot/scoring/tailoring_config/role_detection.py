"""Role Detection."""

"""Config-driven tailoring configuration module.

Loads and validates tailoring configuration from the normalized profile contract,
detects role types from job titles, loads example resumes, and provides
role-specific instructions, constraints, and guidelines.

All configuration is explicit - no magic folders or implicit behavior.
"""

# Default configuration values
DEFAULT_ROLE_TYPE = "general"
DEFAULT_BULLET_TEMPLATE = "CAR"
DEFAULT_MAX_BULLETS_PER_ROLE = 6
DEFAULT_MAX_SUMMARY_LINES = 4
DEFAULT_SELECTED_IMPACT_METRICS = 5


def detect_role_type(job_title: str, config: dict) -> str:
    """Detect role type from job title using configured keywords.

    Iterates through role_types in config and checks if any detection_keywords
    match the job title (case-insensitive).

    Args:
        job_title: The job title to analyze.
        config: Tailoring configuration dict.

    Returns:
        Role type key (e.g., "software_engineer", "product_manager").
        Returns default_role_type from config or "general" if no match.

    Example:
        >>> config = {
        ...     "role_types": {
        ...         "software_engineer": {"detection_keywords": ["engineer", "developer"]},
        ...         "product_manager": {"detection_keywords": ["product manager", "pm"]}
        ...     }
        ... }
        >>> detect_role_type("Senior Software Engineer", config)
        'software_engineer'
    """
    if not job_title:
        return config.get("default_role_type", DEFAULT_ROLE_TYPE)

    title_lower = job_title.lower()
    role_types = config.get("role_types", {})

    for role_type, role_config in role_types.items():
        keywords = role_config.get("detection_keywords", [])
        if any(kw.lower() in title_lower for kw in keywords):
            return role_type

    return config.get("default_role_type", DEFAULT_ROLE_TYPE)


def get_role_instructions(role_type: str, config: dict) -> dict:
    """Get all instructions for a role type.

    Args:
        role_type: Role type key from config.
        config: Tailoring configuration dict.

    Returns:
        Dict with keys:
            - instructions: Role-specific instructions dict
            - constraints: Role-specific constraints dict
            - guidelines: Role-specific guidelines dict
            - positioning_frame: Positioning frame string
            - title_variants: List of acceptable title variants

    Example:
        >>> result = get_role_instructions("software_engineer", config)
        >>> result["instructions"]["bullet_template"]
        'CAR'
    """
    role_config = config.get("role_types", {}).get(role_type, {})

    return {
        "instructions": role_config.get("instructions", {}),
        "constraints": role_config.get("constraints", {}),
        "guidelines": role_config.get("guidelines", {}),
        "positioning_frame": role_config.get("positioning_frame", ""),
        "title_variants": role_config.get("title_variants", []),
        "label": role_config.get("label", role_type.replace("_", " ").title()),
    }


def list_role_types(config: dict) -> list[str]:
    """List all configured role types.

    Args:
        config: Tailoring configuration dict.

    Returns:
        List of role type keys.
    """
    return list(config.get("role_types", {}).keys())


def get_role_detection_keywords(role_type: str, config: dict) -> list[str]:
    """Get detection keywords for a role type.

    Args:
        role_type: Role type key.
        config: Tailoring configuration dict.

    Returns:
        List of detection keywords.
    """
    role_config = config.get("role_types", {}).get(role_type, {})
    return role_config.get("detection_keywords", [])
