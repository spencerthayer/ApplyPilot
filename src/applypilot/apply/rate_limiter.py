"""Per-portal token bucket rate limiter (LLD §17.4).

Default: 5 applications per hour per portal domain.
Randomized delays to avoid detection patterns.
"""

from __future__ import annotations

import random
import threading
import time
from collections import defaultdict
from urllib.parse import urlparse


def extract_portal(url: str) -> str:
    """Extract portal domain from application URL."""
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return "unknown"


class PortalRateLimiter:
    """Token bucket rate limiter, keyed by portal domain."""

    def __init__(self, max_per_hour: int = 5, jitter_seconds: float = 5.0):
        self._max = max_per_hour
        self._jitter = jitter_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def acquire(self, portal: str) -> float:
        """Returns seconds to wait before next application. 0 = proceed now."""
        with self._lock:
            now = time.time()
            window = now - 3600
            self._buckets[portal] = [t for t in self._buckets[portal] if t > window]
            if len(self._buckets[portal]) >= self._max:
                oldest = self._buckets[portal][0]
                wait = oldest - window + random.uniform(0, self._jitter)
                return max(0.0, wait)
            self._buckets[portal].append(now)
            return 0.0

    def wait_and_acquire(self, url: str) -> None:
        """Block until rate limit allows, then acquire a slot."""
        portal = extract_portal(url)
        while True:
            wait = self.acquire(portal)
            if wait <= 0:
                return
            time.sleep(wait)

    def status(self) -> dict[str, int]:
        """Return current usage per portal."""
        with self._lock:
            now = time.time()
            window = now - 3600
            return {portal: len([t for t in times if t > window]) for portal, times in self._buckets.items()}
