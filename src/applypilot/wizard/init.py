"""Init wizard — re-exports."""

from applypilot.wizard.wizard import run_wizard  # noqa: F401
from applypilot.wizard.env_setup import (  # noqa: F401
    _build_ai_env_lines,
    _setup_ai_features,
    _setup_auto_apply,
)
from applypilot.wizard.resume_setup import _setup_resume, _setup_canonical_resume  # noqa: F401
from applypilot.wizard.profile_setup import _setup_profile  # noqa: F401
