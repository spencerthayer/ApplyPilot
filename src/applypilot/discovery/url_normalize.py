"""Rewrite embedded-ATS URLs to their canonical form.

Many companies (Databricks, Stripe, Pinterest, Airbnb, …) embed a
Greenhouse application form as an iframe on their own careers page. The
parent URL carries a ``?gh_jid=N`` query param that identifies the
underlying Greenhouse job. The agent can fill the form much faster on
the iframe's source URL directly (no parent-page noise, no captcha
iframe, smaller a11y snapshots) — so we rewrite at discovery / enrich
time before the apply pipeline ever sees the parent URL.

Canonical Greenhouse form:
    https://job-boards.greenhouse.io/{slug}/jobs/{gh_jid}

Slug sources, in priority order:
  1. ``GREENHOUSE_HOST_SLUGS`` — curated map (top hosts in the queue),
     verified by hand.
  2. Runtime-discovered cache at ``~/.applypilot/greenhouse_slugs_runtime.json``,
     populated by the enrichment scraper when it parses an iframe ``src``
     pointing at ``job-boards.greenhouse.io/{slug}/jobs/{id}``. Lets the
     map grow organically without code changes.

Add an entry to the curated map (or call ``register_runtime_slug``) when
a new employer shows up. Verify with::

    curl -sIo /dev/null -w '%{http_code}\\n' \\
        https://job-boards.greenhouse.io/{slug}/jobs/{any_gh_jid}

200/301/302 means the slug is valid; 404 means it isn't.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


# Bare host (no leading "www.") → Greenhouse tenant slug.
# Verified 2026-04-26 by probing job-boards.greenhouse.io/{slug}/jobs/{id}.
GREENHOUSE_HOST_SLUGS: dict[str, str] = {
    "stripe.com":              "stripe",
    "databricks.com":          "databricks",
    "pinterestcareers.com":    "pinterest",
    "careers.airbnb.com":      "airbnb",
    "jobs.dropbox.com":        "dropbox",
    "cast.ai":                 "castai",
    "sproutsocial.com":        "sproutsocial",
    "samsara.com":             "samsara",
    "instacart.careers":       "instacart",
    "hubspot.com":             "hubspot",
    "kentik.com":              "kentik",
    "consensys.io":            "consensys",
    "abnormal.ai":             "abnormalsecurity",
    "careers.toasttab.com":    "toast",
    "netskope.com":            "netskope",
    "upsun.com":               "upsun",
    "prizepicks.com":          "prizepicks",
    "fortisgames.com":         "fortisgames",
    "kaseya.com":              "kaseya",
    "nebius.com":              "nebius",
}


# Runtime cache for slugs discovered via iframe parsing during enrich.
# Populated by ``register_runtime_slug``; consulted by
# ``canonicalize_application_url`` after the static map.
_RUNTIME_SLUGS_PATH: Path | None = None
_runtime_slugs_cache: dict[str, str] | None = None
_runtime_slugs_lock = threading.Lock()


def _runtime_slugs_path() -> Path:
    """Resolve the on-disk path for the runtime slug cache.

    Lazy import of ``applypilot.config`` so this module can be imported
    in environments that don't have the full config bootstrapped (tests).
    """
    global _RUNTIME_SLUGS_PATH
    if _RUNTIME_SLUGS_PATH is None:
        from applypilot import config as _cfg
        _RUNTIME_SLUGS_PATH = _cfg.APP_DIR / "greenhouse_slugs_runtime.json"
    return _RUNTIME_SLUGS_PATH


def _load_runtime_slugs() -> dict[str, str]:
    global _runtime_slugs_cache
    if _runtime_slugs_cache is not None:
        return _runtime_slugs_cache
    with _runtime_slugs_lock:
        if _runtime_slugs_cache is not None:
            return _runtime_slugs_cache
        path = _runtime_slugs_path()
        if path.exists():
            try:
                _runtime_slugs_cache = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                logger.debug("Could not load runtime slug cache", exc_info=True)
                _runtime_slugs_cache = {}
        else:
            _runtime_slugs_cache = {}
    return _runtime_slugs_cache


def register_runtime_slug(host: str, slug: str) -> bool:
    """Persist a host→slug mapping discovered at runtime.

    Returns True when a new entry was written (or an existing one was
    updated); False when the curated static map already had the same
    binding (we don't need to record duplicates).
    """
    if not host or not slug:
        return False
    host_lc = host.lower()
    if host_lc.startswith("www."):
        host_lc = host_lc[4:]
    # Skip if the static map already has this binding.
    if GREENHOUSE_HOST_SLUGS.get(host_lc) == slug:
        return False
    # Hydrate the in-memory cache BEFORE acquiring the write lock —
    # _load_runtime_slugs takes the same non-reentrant lock internally,
    # which would deadlock if we held it here.
    _load_runtime_slugs()
    with _runtime_slugs_lock:
        cache = _runtime_slugs_cache  # populated by the call above
        if cache is None:
            cache = {}
        if cache.get(host_lc) == slug:
            return False
        cache[host_lc] = slug
        try:
            path = _runtime_slugs_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cache, indent=2, sort_keys=True),
                            encoding="utf-8")
            logger.info("Registered runtime greenhouse slug: %s → %s", host_lc, slug)
            return True
        except Exception:
            logger.debug("Could not persist runtime slug cache", exc_info=True)
            return False


def lookup_slug(host: str) -> str | None:
    """Return the greenhouse slug for ``host`` (static map first, then runtime)."""
    if not host:
        return None
    host_lc = host.lower()
    if host_lc.startswith("www."):
        host_lc = host_lc[4:]
    static = GREENHOUSE_HOST_SLUGS.get(host_lc)
    if static:
        return static
    return _load_runtime_slugs().get(host_lc)


def canonicalize_application_url(url: str) -> str:
    """Return the canonical apply URL for ``url`` if a known rewrite applies.

    Currently rewrites:

      * ``https://{host}/...?gh_jid=N`` → ``https://job-boards.greenhouse.io/{slug}/jobs/N``
        when ``{host}`` is in :data:`GREENHOUSE_HOST_SLUGS` or has been
        registered via :func:`register_runtime_slug`.

    All other URLs (including URLs already on a Greenhouse host) are
    returned unchanged.
    """
    if not url or not url.startswith(("http://", "https://")):
        return url

    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    # Already on Greenhouse — nothing to do.
    if "greenhouse.io" in host:
        return url

    # gh_jid embed → canonical Greenhouse URL
    qs = parse_qs(parsed.query)
    gh_jid_vals = qs.get("gh_jid")
    if gh_jid_vals:
        gh_jid = gh_jid_vals[0]
        if gh_jid.isdigit():
            slug = lookup_slug(host)
            if slug:
                return f"https://job-boards.greenhouse.io/{slug}/jobs/{gh_jid}"

    return url


def parse_greenhouse_slug_from_iframe_src(iframe_src: str) -> str | None:
    """Pull the tenant slug out of a job-boards.greenhouse.io iframe URL.

    Accepts both the new (``job-boards.greenhouse.io``) and legacy
    (``boards.greenhouse.io``) hosts, and the ``/embed/job_app?for=…``
    embed-iframe form. Returns None for anything that doesn't look like
    a Greenhouse iframe.
    """
    if not iframe_src or not iframe_src.startswith(("http://", "https://")):
        return None
    parsed = urlparse(iframe_src)
    host = parsed.netloc.lower()
    if not host.endswith("greenhouse.io"):
        return None
    # Form 1: /embed/job_app?for=SLUG&...
    if parsed.path.startswith("/embed/job_app"):
        for_vals = parse_qs(parsed.query).get("for")
        if for_vals and for_vals[0]:
            return for_vals[0]
    # Form 2: /{slug}/jobs/{id}
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 3 and parts[1] == "jobs" and parts[2].isdigit():
        return parts[0]
    return None
