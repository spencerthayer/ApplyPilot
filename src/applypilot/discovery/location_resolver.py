"""Resolve country from job location strings using local-geocode.

Uses the local-geocode library (offline, GeoNames data) to resolve
city/state/country names to ISO country codes. Handles US states,
international cities, alternative names, and ambiguous locations.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_geocoder():
    from geocode.geocode import Geocode
    gc = Geocode()
    gc.load()
    return gc


def resolve_country(location: str) -> str | None:
    """Extract country code from a location string. Returns ISO 2-letter code or None.

    Handles: "Palo Alto, California" → US, "Singapore" → SG,
    "Remote - North Carolina" → US, "Hyderabad, India" → IN
    """
    if not location:
        return None

    # Strip remote/hybrid prefixes
    loc = re.sub(r"^(?:remote|hybrid)\s*[-–,:/]\s*", "", location, flags=re.IGNORECASE).strip()
    if not loc or loc.lower() in ("remote", "hybrid", "worldwide", "global", "anywhere"):
        return None

    try:
        gc = _get_geocoder()
        results = gc.decode(loc)
        if not results:
            return None

        # If a country-type result exists, use its code (handles "Hyderabad, India" → IN not PK)
        for r in results:
            if r.get("location_type") == "country":
                return r["country_code"]

        # Otherwise use the first result's country
        return results[0].get("country_code")
    except ImportError:
        log.debug("[location] local-geocode not installed")
        return None
    except Exception as e:
        log.debug("[location] resolve failed for '%s': %s", location[:40], e)
        return None
