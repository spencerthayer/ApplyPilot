"""Config-driven tailoring configuration module.

Loads and validates tailoring configuration from the normalized profile contract,
detects role types from job titles, loads example resumes, and provides
role-specific instructions, constraints, and guidelines.

All configuration is explicit - no magic folders or implicit behavior.
"""

import re
from pathlib import Path

from applypilot.config import PROFILE_PATH, load_profile


# Default configuration values
DEFAULT_ROLE_TYPE = "general"
DEFAULT_BULLET_TEMPLATE = "CAR"
DEFAULT_MAX_BULLETS_PER_ROLE = 6
DEFAULT_MAX_SUMMARY_LINES = 4
DEFAULT_SELECTED_IMPACT_METRICS = 5


def load_tailoring_config(profile: dict | None = None) -> dict:
    """Extract tailoring_config from profile.

    Args:
        profile: User profile dict. If None, loads from PROFILE_PATH.

    Returns:
        Tailoring configuration dict.

    Raises:
        FileNotFoundError: If profile file doesn't exist and profile not provided.
    """
    if profile is None:
        try:
            profile = load_profile()
        except FileNotFoundError:
            raise FileNotFoundError(f"Profile not found at {PROFILE_PATH}. Run `applypilot init` first.") from None

    # profile may be None for static analyzers; guard defensively
    return (profile or {}).get("tailoring_config", {})


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


def load_examples(role_type: str, config: dict) -> list[dict]:
    """Load example resumes for a role type.

    Reads example resume files referenced by path in the config.
    Paths are expanded (supports ~ for home directory).

    Args:
        role_type: Role type key from config.
        config: Tailoring configuration dict.

    Returns:
        List of example resume dicts with keys:
            - name: Example name from config
            - content: File content as string
            - description: Optional description
            - quality_score: Optional quality score (0.0-1.0)
            - tags: Optional list of tags
            - path: Resolved path object
            - exists: Boolean indicating if file was found

    Note:
        Missing files are included in the result with exists=False and content="".
        Callers should check the exists flag or filter as needed.
    """
    role_config = config.get("role_types", {}).get(role_type, {})
    examples_config = role_config.get("example_resumes", [])

    examples = []
    for ex_conf in examples_config:
        path_str = ex_conf.get("path", "")
        if not path_str:
            examples.append(
                {
                    "name": ex_conf.get("name", "unnamed"),
                    "content": "",
                    "description": ex_conf.get("description", ""),
                    "quality_score": ex_conf.get("quality_score", 0.0),
                    "tags": ex_conf.get("tags", []),
                    "path": None,
                    "exists": False,
                }
            )
            continue

        # Detect remote URIs (S3 or HTTP(S)) and treat them as remote resources
        if isinstance(path_str, str) and path_str.startswith(("s3://", "http://", "https://")):
            examples.append(
                {
                    "name": ex_conf.get("name", path_str),
                    # We do not attempt to fetch remote content here; leave content blank
                    "content": "",
                    "description": ex_conf.get("description", ""),
                    "quality_score": ex_conf.get("quality_score", 0.0),
                    "tags": ex_conf.get("tags", []),
                    "path": None,
                    "uri": path_str,
                    "remote": True,
                    "exists": False,
                }
            )
            continue

        path = Path(path_str).expanduser()
        exists = path.exists()
        content = ""
        if exists:
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                # If file cannot be read, treat as missing but keep metadata
                content = ""

        examples.append(
            {
                "name": ex_conf.get("name", path.stem),
                "content": content,
                "description": ex_conf.get("description", ""),
                "quality_score": ex_conf.get("quality_score", 0.0),
                "tags": ex_conf.get("tags", []),
                "path": path,
                "remote": False,
                "exists": exists,
            }
        )

    return examples


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


def validate_tailoring_config(config: dict) -> list[str]:
    """Validate tailoring config structure.

    Checks for required fields and valid structure. Returns list of error
    messages. Empty list means config is valid.

    Args:
        config: Tailoring configuration dict.

    Returns:
        List of validation error messages.
    """
    errors = []

    # Check enabled flag exists (optional but recommended)
    if "enabled" not in config:
        errors.append("Missing 'enabled' flag (recommended to include)")

    # Check role_types exist
    if "role_types" not in config:
        errors.append("Missing required field: role_types")
        return errors

    role_types = config.get("role_types", {})
    if not role_types:
        errors.append("role_types is empty - at least one role type required")
        return errors

    # Validate each role type
    for role_type, role_conf in role_types.items():
        if not isinstance(role_conf, dict):
            errors.append(f"Role '{role_type}' must be a dict")
            continue

        # Check detection_keywords
        if "detection_keywords" not in role_conf:
            errors.append(f"Role '{role_type}' missing detection_keywords")
        elif not isinstance(role_conf["detection_keywords"], list):
            errors.append(f"Role '{role_type}' detection_keywords must be a list")

        # Validate example_resumes if present
        examples = role_conf.get("example_resumes", [])
        if not isinstance(examples, list):
            errors.append(f"Role '{role_type}' example_resumes must be a list")
        else:
            for i, ex in enumerate(examples):
                if not isinstance(ex, dict):
                    errors.append(f"Role '{role_type}' example {i} must be a dict")
                    continue
                if "path" not in ex:
                    errors.append(f"Role '{role_type}' example {i} missing path")
                if "name" not in ex:
                    errors.append(f"Role '{role_type}' example {i} missing name")

        # Validate instructions if present
        instructions = role_conf.get("instructions", {})
        if not isinstance(instructions, dict):
            errors.append(f"Role '{role_type}' instructions must be a dict")

        # Validate constraints if present
        constraints = role_conf.get("constraints", {})
        if not isinstance(constraints, dict):
            errors.append(f"Role '{role_type}' constraints must be a dict")

        # Validate guidelines if present
        guidelines = role_conf.get("guidelines", {})
        if not isinstance(guidelines, dict):
            errors.append(f"Role '{role_type}' guidelines must be a dict")

    # Validate global_rules if present
    global_rules = config.get("global_rules", {})
    if global_rules and not isinstance(global_rules, dict):
        errors.append("global_rules must be a dict")
    elif global_rules:
        # Validate role_compression
        compression = global_rules.get("role_compression", {})
        if compression and not isinstance(compression, dict):
            errors.append("global_rules.role_compression must be a dict")

        # Validate formatting
        formatting = global_rules.get("formatting", {})
        if formatting and not isinstance(formatting, dict):
            errors.append("global_rules.formatting must be a dict")

    # Validate quality_gates if present
    quality_gates = config.get("quality_gates", {})
    if quality_gates and not isinstance(quality_gates, dict):
        errors.append("quality_gates must be a dict")

    # Validate evidence_ledger if present
    evidence_ledger = config.get("evidence_ledger", {})
    if evidence_ledger and not isinstance(evidence_ledger, dict):
        errors.append("evidence_ledger must be a dict")

    return errors


def apply_global_rules(content: str, global_rules: dict) -> str:
    """Apply global formatting and compression rules to content.

    Applies rules from global_rules configuration:
    - Date formatting (converts common date formats to specified format)
    - Bullet style normalization
    - Role compression (summarizes older roles)

    Args:
        content: Resume content as string.
        global_rules: Global rules dict from config.

    Returns:
        Modified content with rules applied.

    Note:
        This is a best-effort transformation. Complex formatting may require
        manual review.
    """
    if not content or not global_rules:
        return content

    result = content

    # Apply date formatting
    formatting = global_rules.get("formatting", {})
    date_format = formatting.get("date_format", "YYYY-MM")

    if date_format == "YYYY-MM":
        # Convert common date formats to YYYY-MM
        # Matches: MM/YYYY, M/YYYY, Mon YYYY, Month YYYY
        result = _normalize_dates_to_yyyy_mm(result)

    # Apply bullet style normalization
    bullet_style = formatting.get("bullet_style", "sentence_case")
    if bullet_style == "sentence_case":
        result = _normalize_bullet_case(result)

    # Note: Role compression is typically applied at the tailoring stage
    # before this function is called, as it requires structural knowledge
    # of the resume sections.

    return result


def _normalize_dates_to_yyyy_mm(text: str) -> str:
    """Normalize date strings in text to YYYY-MM format.

    Args:
        text: Text containing dates.

    Returns:
        Text with normalized dates.
    """
    import re

    # Pattern for MM/YYYY or M/YYYY
    slash_pattern = r"\b(\d{1,2})/(\d{4})\b"

    def replace_slash_date(match: re.Match) -> str:
        month = match.group(1).zfill(2)
        year = match.group(2)
        return f"{year}-{month}"

    text = re.sub(slash_pattern, replace_slash_date, text)

    # Pattern for Month YYYY (full and abbreviated)
    month_map = {
        "january": "01",
        "jan": "01",
        "february": "02",
        "feb": "02",
        "march": "03",
        "mar": "03",
        "april": "04",
        "apr": "04",
        "may": "05",
        "june": "06",
        "jun": "06",
        "july": "07",
        "jul": "07",
        "august": "08",
        "aug": "08",
        "september": "09",
        "sep": "09",
        "sept": "09",
        "october": "10",
        "oct": "10",
        "november": "11",
        "nov": "11",
        "december": "12",
        "dec": "12",
    }

    month_pattern = r"\b(" + "|".join(month_map.keys()) + r")\.?\s+(\d{4})\b"

    def replace_month_date(match: re.Match) -> str:
        month_name = match.group(1).lower()
        year = match.group(2)
        month_num = month_map.get(month_name, "01")
        return f"{year}-{month_num}"

    text = re.sub(month_pattern, replace_month_date, text, flags=re.IGNORECASE)

    # Handle ranges like 'Jan 2010 - Mar 2013' or 'Jan 2010 - Present'
    month_names = "|".join(month_map.keys())
    range_month_pattern = re.compile(
        rf"\b({month_names})\.?\s+(\d{{4}})\s*[-–—]\s*({month_names})\.?\s+(\d{{4}}|Present|Current)\b",
        flags=re.IGNORECASE,
    )

    def replace_month_range(match: re.Match) -> str:
        start_month = match.group(1).lower()
        start_year = match.group(2)
        end_month = match.group(3).lower()
        end_year = match.group(4)

        start_mm = month_map.get(start_month, "01")
        if end_year.lower() in ("present", "current"):
            end_part = "Present"
        else:
            end_mm = month_map.get(end_month, "01")
            end_part = f"{end_year}-{end_mm}"

        return f"{start_year}-{start_mm} - {end_part}"

    text = range_month_pattern.sub(replace_month_range, text)

    # Handle simple year ranges like '2010 - 2013' or '2010 - Present'
    year_range_pattern = re.compile(r"\b(\d{4})\s*[-–—]\s*(\d{4}|Present|Current)\b")

    def replace_year_range(match: re.Match) -> str:
        start = match.group(1)
        end = match.group(2)
        if end.lower() in ("present", "current"):
            end_part = "Present"
        else:
            end_part = end
        return f"{start} - {end_part}"

    text = year_range_pattern.sub(replace_year_range, text)

    return text


def _normalize_bullet_case(text: str) -> str:
    """Normalize bullet points to sentence case.

    Args:
        text: Text with bullet points.

    Returns:
        Text with normalized bullet case.
    """
    # Use regex to detect bullets and numbered lists across lines
    pattern = re.compile(r"^(\s*(?:[-*••○◊]|\d+\.\s+|\d+\)\s+))(.*)$", flags=re.MULTILINE)

    def repl(m: re.Match) -> str:
        bullet = m.group(1)
        content = m.group(2).lstrip()
        if not content:
            return m.group(0)

        # Convert first character to lowercase for sentence case
        if len(content) == 1:
            content = content.lower()
        else:
            content = content[0].lower() + content[1:]

        # Ensure single space between bullet and content
        if bullet.endswith(" "):
            return f"{bullet}{content}"
        return f"{bullet} {content}"

    return pattern.sub(repl, text)


def get_global_rules(config: dict) -> dict:
    """Get global rules with defaults applied.

    Args:
        config: Tailoring configuration dict.

    Returns:
        Global rules dict with all keys populated (using defaults where needed).
    """
    global_rules = config.get("global_rules", {})

    return {
        "max_summary_lines": global_rules.get("max_summary_lines", DEFAULT_MAX_SUMMARY_LINES),
        "selected_impact_metrics": global_rules.get("selected_impact_metrics", DEFAULT_SELECTED_IMPACT_METRICS),
        "role_compression": global_rules.get(
            "role_compression",
            {
                "enabled": True,
                "older_than_years": 10,
                "max_bullets_per_old_role": 3,
            },
        ),
        "formatting": global_rules.get(
            "formatting",
            {
                "date_format": "YYYY-MM",
                "bullet_style": "sentence_case",
                "skills_separator": " | ",
            },
        ),
    }


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


def check_banned_phrases(text: str, role_type: str, config: dict) -> list[str]:
    """Check text for banned phrases for a role type.

    Args:
        text: Text to check.
        role_type: Role type key.
        config: Tailoring configuration dict.

    Returns:
        List of banned phrases found in text.
    """
    role_config = config.get("role_types", {}).get(role_type, {})
    constraints = role_config.get("constraints", {})
    banned = constraints.get("banned_phrases", [])

    if not banned:
        return []

    text_lower = text.lower()
    found = []

    for phrase in banned:
        if phrase.lower() in text_lower:
            found.append(phrase)

    return found


def check_required_patterns(text: str, role_type: str, config: dict) -> tuple[list[str], list[str]]:
    """Check text for required patterns for a role type.

    Args:
        text: Text to check.
        role_type: Role type key.
        config: Tailoring configuration dict.

    Returns:
        Tuple of (found_patterns, missing_patterns).
    """
    role_config = config.get("role_types", {}).get(role_type, {})
    constraints = role_config.get("constraints", {})
    required = constraints.get("required_patterns", [])

    if not required:
        return [], []

    text_lower = text.lower()
    found = []
    missing = []

    for pattern in required:
        if pattern.lower() in text_lower:
            found.append(pattern)
        else:
            missing.append(pattern)

    return found, missing


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
    import re
    year_match = re.search(r'(\d{4})', role_dates)
    if year_match:
        year = int(year_match.group(1))
        return year < merge_config["recent_cutoff_year"]

    # Default to not merging if we can't determine
    return False
