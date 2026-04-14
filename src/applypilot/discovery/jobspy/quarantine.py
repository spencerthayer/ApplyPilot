"""Quarantine."""

__all__ = [
    "_JOBSPY_PARAMS",
    "_load_site_quarantines",
    "_save_site_quarantines",
    "_quarantine_site",
    "_quarantine_reason_for_exception",
]

"""JobSpy-based job discovery: searches Indeed, LinkedIn, Glassdoor, ZipRecruiter.

Uses python-jobspy to scrape multiple job boards, deduplicates results,
parses salary ranges, and stores everything in the ApplyPilot database.

Search queries, locations, and filtering rules are loaded from the user's
search configuration YAML (searches.yaml) rather than being hardcoded.
"""

import inspect as _inspect
import json
from datetime import datetime, timedelta, timezone

from jobspy import scrape_jobs as _raw_scrape_jobs

# Only pass params that the installed jobspy version actually accepts.
_JOBSPY_PARAMS = set(_inspect.signature(_raw_scrape_jobs).parameters.keys())

from applypilot.discovery.jobspy.constants import (
    _JOBSPY_SITE_QUARANTINE_PATH,
    _JOBSPY_SITE_QUARANTINE_HOURS,
)


def _load_site_quarantines() -> dict[str, dict]:
    """Load active JobSpy site quarantines from user state."""
    if not _JOBSPY_SITE_QUARANTINE_PATH.exists():
        return {}

    try:
        payload = json.loads(_JOBSPY_SITE_QUARANTINE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    now = datetime.now(timezone.utc)
    active: dict[str, dict] = {}
    for site, info in payload.items():
        try:
            until = datetime.fromisoformat(info["until"])
        except (KeyError, TypeError, ValueError):
            continue
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        until = until.astimezone(timezone.utc)
        if until > now:
            active[site] = {
                "until": until,
                "reason": info.get("reason", "unknown"),
            }
    return active


def _save_site_quarantines(quarantines: dict[str, dict]) -> None:
    serializable = {
        site: {
            "until": info["until"].astimezone(timezone.utc).isoformat(),
            "reason": info.get("reason", "unknown"),
        }
        for site, info in quarantines.items()
    }
    try:
        _JOBSPY_SITE_QUARANTINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _JOBSPY_SITE_QUARANTINE_PATH.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    except OSError:
        return


def _quarantine_site(site: str, reason: str) -> dict:
    active = _load_site_quarantines()
    existing = active.get(site)
    if existing is not None:
        return existing

    info = {
        "until": datetime.now(timezone.utc) + timedelta(hours=_JOBSPY_SITE_QUARANTINE_HOURS),
        "reason": reason,
    }
    active[site] = info
    _save_site_quarantines(active)
    return info


def _quarantine_reason_for_exception(site: str, exc: Exception) -> str | None:
    text = f"{exc.__class__.__name__}: {exc}".lower()
    if site == "zip_recruiter" and (
            "403" in text or "cloudflare" in text or "challenge" in text or "forbidden" in text
    ):
        return "cloudflare_403"
    return None
