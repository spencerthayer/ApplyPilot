"""Filters."""

__all__ = [
    "_JOBSPY_PARAMS",
    "_location_ok",
    "_load_location_config",
    "_coerce_posted_datetime",
    "_apply_local_hours_filter",
    "_coerce_distance",
    "_resolve_search_distance",
    "_resolve_jobspy_sites",
]

"""JobSpy-based job discovery: searches Indeed, LinkedIn, Glassdoor, ZipRecruiter.

Uses python-jobspy to scrape multiple job boards, deduplicates results,
parses salary ranges, and stores everything in the ApplyPilot database.

Search queries, locations, and filtering rules are loaded from the user's
search configuration YAML (searches.yaml) rather than being hardcoded.
"""

import inspect as _inspect
from datetime import date, datetime, time as dtime, timedelta, timezone

from jobspy import scrape_jobs as _raw_scrape_jobs

# Only pass params that the installed jobspy version actually accepts.
_JOBSPY_PARAMS = set(_inspect.signature(_raw_scrape_jobs).parameters.keys())


def _location_ok(location: str | None, accept: list[str], reject: list[str], mode: str = "include_only") -> bool:
    """Check if a job location passes the user's geographic filter (RUN-09).

    Modes:
      - "worldwide": accept everything except explicit rejects
      - "include_only": only accept locations matching accept patterns
      - "exclude": accept everything except reject patterns

    Remote jobs are always accepted regardless of mode.
    """
    if not location:
        return True  # unknown location -- keep it, let scorer decide

    loc = location.lower()

    # Remote jobs always OK
    if any(r in loc for r in ("remote", "anywhere", "work from home", "wfh", "distributed")):
        return True

    # Always apply reject patterns
    for r in reject:
        if r.lower() in loc:
            return False

    if mode == "worldwide":
        return True

    if mode == "exclude":
        return True  # rejects already handled above

    # include_only (default): must match an accept pattern
    if not accept:
        return True  # no accept list = accept all
    for a in accept:
        if a.lower() in loc:
            return True
    return False


def _load_location_config(search_cfg: dict) -> tuple[list[str], list[str], str]:
    """Extract accept/reject location lists and mode from search config.

    Returns:
        (accept_patterns, reject_patterns, mode)
        mode is one of: "worldwide", "include_only", "exclude"
    """
    loc_cfg = search_cfg.get("location", {})
    accept = loc_cfg.get("accept_patterns", search_cfg.get("location_accept", []))
    reject = loc_cfg.get("reject_patterns", search_cfg.get("location_reject_non_remote", []))
    mode = loc_cfg.get("mode", "include_only")
    if mode not in ("worldwide", "include_only", "exclude"):
        mode = "include_only"
    return accept, reject, mode


def _coerce_posted_datetime(value) -> datetime | None:
    """Convert a JobSpy date_posted cell into a timezone-aware datetime."""
    if value is None:
        return None

    try:
        if hasattr(value, "to_pydatetime"):
            value = value.to_pydatetime()
    except Exception:
        pass

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, dtime.min)
    else:
        raw = str(value).strip()
        if not raw or raw == "nan":
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y"):
                try:
                    dt = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _apply_local_hours_filter(df, hours_old: int) -> tuple[object, int, bool]:
    """Apply a best-effort local recency filter using the date_posted column."""
    if hours_old <= 0 or "date_posted" not in df.columns:
        return df, 0, False

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_old)
    kept_rows: list[bool] = []
    seen_posted_value = False

    for value in df["date_posted"]:
        posted_dt = _coerce_posted_datetime(value)
        if posted_dt is None:
            kept_rows.append(True)
            continue
        seen_posted_value = True
        kept_rows.append(posted_dt >= cutoff)

    if not seen_posted_value:
        return df, 0, False

    filtered_df = df[kept_rows]
    removed = len(df) - len(filtered_df)
    return filtered_df, removed, True


def _coerce_distance(value: object) -> int | None:
    """Parse a search distance (miles) value from config or CLI."""
    if value is None:
        return None
    try:
        miles = int(value)
    except (TypeError, ValueError):
        return None
    return miles if miles > 0 else None


def _resolve_search_distance(search: dict, defaults: dict | None = None) -> int | None:
    """Resolve effective search distance (miles) for one query/location combo."""
    if search.get("remote"):
        return None
    if "distance" in search:
        return _coerce_distance(search.get("distance"))
    defaults = defaults or {}
    return _coerce_distance(defaults.get("distance"))


def _resolve_jobspy_sites(sites: list[str], remote_only: bool) -> tuple[list[str], bool]:
    """Return non-Glassdoor sites after ApplyPilot's remote-only adjustments."""
    has_glassdoor = "glassdoor" in sites
    effective_sites = [site for site in sites if site != "glassdoor"]
    if remote_only and "indeed" in effective_sites:
        effective_sites = [site for site in effective_sites if site != "indeed"]
    return effective_sites, has_glassdoor


# ── Remote work mode filtering (RUN-10) ────────────────────────────────

_REMOTE_KEYWORDS = {"remote", "anywhere", "work from home", "wfh", "distributed", "telecommute"}
_HYBRID_KEYWORDS = {"hybrid", "flexible"}


def classify_work_mode(location: str | None, description: str | None = None) -> str:
    """Classify a job's work mode from location/description text.

    Returns one of: "remote", "hybrid", "onsite", "unknown"
    """
    text = ((location or "") + " " + (description or "")[:500]).lower()
    if any(kw in text for kw in _REMOTE_KEYWORDS):
        return "remote"
    if any(kw in text for kw in _HYBRID_KEYWORDS):
        return "hybrid"
    if location and location.strip():
        return "onsite"
    return "unknown"


def work_mode_ok(location: str | None, description: str | None, allowed_modes: list[str] | None = None) -> bool:
    """Check if a job's work mode matches the user's preference (RUN-10).

    Args:
        allowed_modes: e.g. ["remote", "hybrid"]. None = accept all.
    """
    if not allowed_modes:
        return True
    mode = classify_work_mode(location, description)
    if mode == "unknown":
        return True  # don't filter unknowns
    return mode in allowed_modes
