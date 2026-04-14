"""LLM response cache via LLMCacheRepository (LLD §12.2).

Transparent proxy — wraps any LLMClient, caches responses keyed by
(messages + model + temperature). All other methods delegate directly.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from applypilot.db.dto import LLMCacheEntryDTO
from applypilot.db.interfaces.llm_cache_repository import LLMCacheRepository

log = logging.getLogger(__name__)


def _hash_key(messages: list, model: str, temperature: float | None) -> str:
    raw = json.dumps(messages, sort_keys=True) + model + str(temperature or 0)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class CachedLLMClient:
    """Transparent caching proxy for LLMClient.

    Delegates all attributes to the wrapped client. Only `chat()` is
    intercepted for cache lookup/store.
    """

    def __init__(self, client: Any, llm_cache_repo: LLMCacheRepository):
        self._client = client
        self._cache = llm_cache_repo
        self._hits = 0
        self._misses = 0

    def __getattr__(self, name: str) -> Any:
        """Proxy all attributes to the wrapped client."""
        return getattr(self._client, name)

    def chat(self, messages: list, **kwargs: Any) -> str:
        temperature = kwargs.get("temperature")
        cache_key = _hash_key(messages, self._client.model, temperature)

        if cached := self._cache.get_by_key(cache_key):
            self._cache.increment_hit(cache_key)
            self._hits += 1
            log.debug("[cache] HIT %s (hits=%d)", cache_key[:8], cached.hit_count + 1)
            return cached.response

        self._misses += 1
        response = self._client.chat(messages, **kwargs)

        self._cache.save(
            LLMCacheEntryDTO(
                cache_key=cache_key,
                response=response,
                model=self._client.model,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        return response

    @property
    def cache_stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses}
