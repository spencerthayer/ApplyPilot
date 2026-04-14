"""Shared constants for jobspy discovery."""

from __future__ import annotations

__all__ = [
    "_HOURS_OLD_SUPPORTED",
    "_HOURS_OLD_WARNING_EMITTED",
    "_DEBUG_JOBSPY_ENABLED",
    "_DEBUG_LOG_PATH",
    "_DEBUG_SESSION_ID",
    "_DEBUG_RUN_ID",
    "_JOBSPY_SITE_QUARANTINE_PATH",
    "_JOBSPY_SITE_QUARANTINE_HOURS",
    "_STEALTH_INIT_SCRIPT",
]

import inspect
import os
from pathlib import Path

from applypilot import config

try:
    from jobspy import scrape_jobs as _raw_scrape_jobs

    _SCRAPE_JOBS_PARAMS = set(inspect.signature(_raw_scrape_jobs).parameters)
except Exception:
    _SCRAPE_JOBS_PARAMS = set()

_HOURS_OLD_SUPPORTED = "hours_old" in _SCRAPE_JOBS_PARAMS
_HOURS_OLD_WARNING_EMITTED = False
_HOURS_OLD_WARNING_EMITTED = False
_DEBUG_JOBSPY_ENABLED = os.getenv("APPLYPILOT_DEBUG_JOBSPY") == "1"
_DEBUG_LOG_PATH = Path(os.getenv("APPLYPILOT_DEBUG_LOG_PATH", ".cursor/debug-e0a421.log")).expanduser()
_DEBUG_SESSION_ID = os.getenv("APPLYPILOT_DEBUG_SESSION_ID", "e0a421")
_DEBUG_RUN_ID = os.getenv("APPLYPILOT_DEBUG_RUN_ID", "default")
_JOBSPY_SITE_QUARANTINE_PATH = config.APP_DIR / "jobspy_site_quarantine.json"
_JOBSPY_SITE_QUARANTINE_HOURS = 12

_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
"""
