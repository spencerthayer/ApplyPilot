"""Default values and environment loading."""

from __future__ import annotations

import os
from collections.abc import Mapping

from applypilot.config.paths import ENV_PATH

DEFAULTS = {
    "min_score": 7,
    "max_apply_attempts": 10,
    "max_tailor_attempts": 10,
    "poll_interval": 30,
    "apply_timeout": 300,
    "viewport": "1280x900",
}


def get_runtime_defaults() -> dict:
    """Return DEFAULTS merged with RuntimeConfig values if available."""
    try:
        from applypilot.bootstrap import get_app

        rc = get_app().config
        return {
            "min_score": rc.scoring.min_score,
            "max_apply_attempts": rc.apply.max_attempts,
            "max_tailor_attempts": rc.tailoring.max_attempts,
            "poll_interval": rc.apply.poll_interval_seconds,
            "apply_timeout": rc.apply.timeout_seconds,
            "viewport": rc.apply.viewport,
        }
    except Exception:
        return DEFAULTS


def load_env():
    """Load environment variables from ~/.applypilot/.env if it exists."""
    from dotenv import load_dotenv

    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)
    load_dotenv()


def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ
