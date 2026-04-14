"""Loader."""

"""Config-driven tailoring configuration module.

Loads and validates tailoring configuration from the normalized profile contract,
detects role types from job titles, loads example resumes, and provides
role-specific instructions, constraints, and guidelines.

All configuration is explicit - no magic folders or implicit behavior.
"""

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
