"""Rules."""

"""Config-driven tailoring configuration module.

Loads and validates tailoring configuration from the normalized profile contract,
detects role types from job titles, loads example resumes, and provides
role-specific instructions, constraints, and guidelines.

All configuration is explicit - no magic folders or implicit behavior.
"""

import re

# Default configuration values
DEFAULT_ROLE_TYPE = "general"
DEFAULT_BULLET_TEMPLATE = "CAR"
DEFAULT_MAX_BULLETS_PER_ROLE = 6
DEFAULT_MAX_SUMMARY_LINES = 4
DEFAULT_SELECTED_IMPACT_METRICS = 5


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
