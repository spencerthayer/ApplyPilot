"""Execution mode tags — headless vs headful capability detection.

Used to determine which operations are safe for server deployment
(no Chrome, no human, no display).
"""

from __future__ import annotations

import os
from enum import StrEnum


class ExecutionMode(StrEnum):
    HEADLESS = "headless"  # No Chrome, no human, no display
    HEADFUL = "headful"  # Chrome available, human may be present


def detect_mode() -> ExecutionMode:
    """Detect execution mode from environment."""
    # Explicit override
    if os.environ.get("APPLYPILOT_MODE", "").lower() == "headless":
        return ExecutionMode.HEADLESS
    # No display = headless
    if not os.environ.get("DISPLAY") and os.name != "nt" and not os.environ.get("TERM_PROGRAM"):
        # macOS doesn't use DISPLAY — check for WindowServer instead
        import sys
        if sys.platform == "darwin":
            return ExecutionMode.HEADFUL
        return ExecutionMode.HEADLESS
    return ExecutionMode.HEADFUL


def is_headless() -> bool:
    return detect_mode() == ExecutionMode.HEADLESS


# Operations tagged by mode requirement
HEADLESS_SAFE = {
    "discover",
    "enrich",
    "score",
    "tailor",
    "cover",
    "analytics",
    "status",
    "dashboard",
    "resume_refresh",
    "tracks_discover",
    "tracks_list",
}

HEADFUL_REQUIRED = {
    "apply",
    "human_review",
}

# Operations that PREFER headful but can degrade
HEADFUL_PREFERRED = {
    "smart_extract",  # falls back to headless if no Chrome
    "resume_render",  # needs npx but not Chrome
}
