"""Session — extracted from chrome/__init__.py."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

from applypilot.apply.classifier.constants import ATS_DOMAINS
from applypilot.apply.chrome.profile import _copy_auth_files
from applypilot.config.paths import SESSIONS_DIR

logger = logging.getLogger(__name__)


def detect_ats(url: str | None) -> str | None:
    """Detect the ATS platform from a job or application URL.

    Returns:
        ATS slug (e.g., 'workday') or None if no known ATS detected.
    """
    if not url:
        return None
    try:
        host = urlparse(url).hostname or ""
        host = host.lower()
        for domain, slug in ATS_DOMAINS.items():
            if domain in host:
                return slug
    except Exception:
        pass
    return None


def get_ats_session_path(ats_slug: str) -> Path:
    """Get the persistent session directory for an ATS platform."""
    return SESSIONS_DIR / ats_slug


def save_ats_session(worker_profile_dir: Path, ats_slug: str) -> int:
    """Save auth-essential files from a worker profile to the ATS session dir.

    Called after a successful HITL login or apply on an ATS. Persists
    cookies, login data, and local storage so future workers can reuse
    the authenticated session.

    Args:
        worker_profile_dir: Path to the worker's Chrome user-data dir.
        ats_slug: ATS platform slug (e.g., 'workday').

    Returns:
        Number of files copied.
    """
    session_dir = get_ats_session_path(ats_slug)
    count = _copy_auth_files(worker_profile_dir, session_dir)
    if count:
        logger.info("Saved %d auth files to ATS session: %s", count, ats_slug)
    return count


def clear_ats_session(ats_slug: str) -> bool:
    """Remove a stale ATS session (e.g., expired cookies).

    Returns:
        True if a session was removed.
    """
    session_dir = get_ats_session_path(ats_slug)
    if session_dir.exists():
        shutil.rmtree(str(session_dir), ignore_errors=True)
        logger.info("Cleared stale ATS session: %s", ats_slug)
        return True
    return False


def list_ats_sessions() -> list[dict]:
    """List all saved ATS sessions with their age.

    Returns:
        List of dicts with keys: slug, path, age_hours, has_cookies.
    """
    sessions = []
    if not SESSIONS_DIR.exists():
        return sessions
    for entry in sorted(SESSIONS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        cookies = entry / "Default" / "Cookies"
        age_hours = None
        if cookies.exists():
            import os

            mtime = os.path.getmtime(cookies)
            age_hours = (time.time() - mtime) / 3600
        sessions.append(
            {
                "slug": entry.name,
                "path": str(entry),
                "age_hours": age_hours,
                "has_cookies": cookies.exists(),
            }
        )
    return sessions
