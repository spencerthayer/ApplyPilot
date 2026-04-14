"""Debug."""

__all__ = [
    "_JOBSPY_PARAMS",
    "_resolve_debug_log_path",
    "_emit_debug_log",
    "_jobspy_debug_compat_snapshot",
    "_clean",
    "parse_proxy",
    "_warn_hours_old_fallback",
]

"""JobSpy-based job discovery: searches Indeed, LinkedIn, Glassdoor, ZipRecruiter.

Uses python-jobspy to scrape multiple job boards, deduplicates results,
parses salary ranges, and stores everything in the ApplyPilot database.

Search queries, locations, and filtering rules are loaded from the user's
search configuration YAML (searches.yaml) rather than being hardcoded.
"""

import inspect as _inspect
import logging

log = logging.getLogger(__name__)
import json
import time
from importlib import metadata as importlib_metadata
from pathlib import Path
from uuid import uuid4

from jobspy import scrape_jobs as _raw_scrape_jobs

# Only pass params that the installed jobspy version actually accepts.
_JOBSPY_PARAMS = set(_inspect.signature(_raw_scrape_jobs).parameters.keys())

from applypilot.discovery.jobspy.constants import (
    _SCRAPE_JOBS_PARAMS,
    _HOURS_OLD_SUPPORTED,
    _DEBUG_JOBSPY_ENABLED,
    _DEBUG_LOG_PATH,
    _DEBUG_SESSION_ID,
    _DEBUG_RUN_ID,
    _HOURS_OLD_WARNING_EMITTED,
)


def _resolve_debug_log_path() -> Path:
    path = _DEBUG_LOG_PATH
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _emit_debug_log(
        hypothesis_id: str,
        location: str,
        message: str,
        data: dict,
        run_id: str = _DEBUG_RUN_ID,
) -> None:
    if not _DEBUG_JOBSPY_ENABLED:
        return
    log_path = _resolve_debug_log_path()
    payload = {
        "sessionId": _DEBUG_SESSION_ID,
        "id": f"log_{int(time.time() * 1000)}_{uuid4().hex[:8]}",
        "timestamp": int(time.time() * 1000),
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except Exception:
        pass


def _jobspy_debug_compat_snapshot() -> dict:
    try:
        version = importlib_metadata.version("python-jobspy")
    except importlib_metadata.PackageNotFoundError:
        version = "unknown"
    return {
        "python_jobspy_version": version,
        "scrape_jobs_signature_params": sorted(_SCRAPE_JOBS_PARAMS),
        "hours_old_supported": _HOURS_OLD_SUPPORTED,
        "site_by_site_fallback_enabled": True,
    }


def _clean(val) -> str | None:
    if val is None:
        return None
    s = str(val)
    return s if s and s != "nan" else None


def parse_proxy(proxy_str: str) -> dict:
    """Parse host:port:user:pass into components."""
    parts = proxy_str.split(":")
    if len(parts) == 4:
        host, port, user, passwd = parts
        return {
            "host": host,
            "port": port,
            "user": user,
            "pass": passwd,
            "jobspy": f"{user}:{passwd}@{host}:{port}",
            "playwright": {
                "server": f"http://{host}:{port}",
                "username": user,
                "password": passwd,
            },
        }
    elif len(parts) == 2:
        host, port = parts
        return {
            "host": host,
            "port": port,
            "user": None,
            "pass": None,
            "jobspy": f"{host}:{port}",
            "playwright": {"server": f"http://{host}:{port}"},
        }
    else:
        raise ValueError(f"Proxy format not recognized: {proxy_str}. Expected: host:port:user:pass or host:port")


def _warn_hours_old_fallback() -> None:
    global _HOURS_OLD_WARNING_EMITTED
    if _HOURS_OLD_WARNING_EMITTED:
        return
    _HOURS_OLD_WARNING_EMITTED = True
    log.warning(
        "Installed JobSpy does not support server-side hours_old filtering; applying best-effort local recency checks."
    )
