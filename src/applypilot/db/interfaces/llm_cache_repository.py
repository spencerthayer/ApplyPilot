"""LLMCacheRepository ABC — contract for LLM response cache persistence."""

from abc import ABC, abstractmethod

from applypilot.db.dto import LLMCacheEntryDTO


class LLMCacheRepository(ABC):
    @abstractmethod
    def get_by_key(self, cache_key: str) -> LLMCacheEntryDTO | None: ...

    @abstractmethod
    def save(self, entry: LLMCacheEntryDTO) -> None: ...

    @abstractmethod
    def increment_hit(self, cache_key: str) -> None: ...
