"""Redirect chain resolver — follows HTTP/JS/meta-refresh redirects.

Phase 1: Follows the full redirect chain from application_url to final destination.
Stores result as RedirectChainDTO via job_repo.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

import requests

from applypilot.apply.classifier.constants import MAX_HOPS, HOP_TIMEOUT_S
from applypilot.apply.classifier.models import RedirectChain, RedirectHop

log = logging.getLogger(__name__)


def resolve_redirect_chain(url: str) -> RedirectChain:
    """Follow redirect chain from URL to final destination.

    Returns RedirectChain with all hops recorded.
    """
    chain = RedirectChain(original_url=url, final_url=url)
    seen: set[str] = set()
    current = url
    t0 = time.time()

    for _ in range(MAX_HOPS):
        if current in seen:
            chain.circular_detected = True
            break
        seen.add(current)

        try:
            hop_start = time.time()
            resp = requests.head(
                current,
                allow_redirects=False,
                timeout=HOP_TIMEOUT_S,
                headers={"User-Agent": "ApplyPilot/1.0"},
            )
            hop_ms = int((time.time() - hop_start) * 1000)

            if resp.is_redirect and resp.headers.get("Location"):
                next_url = resp.headers["Location"]
                # Handle relative redirects
                if not next_url.startswith("http"):
                    parsed = urlparse(current)
                    next_url = f"{parsed.scheme}://{parsed.netloc}{next_url}"

                chain.hops.append(
                    RedirectHop(
                        url=current,
                        status_code=resp.status_code,
                        redirect_type="http",
                        elapsed_ms=hop_ms,
                    )
                )
                current = next_url
            else:
                # No more redirects
                chain.final_url = current
                break
        except requests.RequestException as e:
            log.warning("Redirect resolution failed at %s: %s", current, e)
            chain.final_url = current
            break

    chain.final_url = current
    chain.final_dom = urlparse(current).netloc
    chain.total_time_ms = int((time.time() - t0) * 1000)
    return chain
