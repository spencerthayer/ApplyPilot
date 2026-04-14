"""SqliteLLMCacheRepository — concrete LLMCacheRepository for SQLite."""

from __future__ import annotations

from applypilot.db.dto import LLMCacheEntryDTO
from applypilot.db.interfaces.llm_cache_repository import LLMCacheRepository
from applypilot.db.sqlite.base_repo import SqliteBaseRepo


class SqliteLLMCacheRepository(SqliteBaseRepo, LLMCacheRepository):
    def get_by_key(self, cache_key: str) -> LLMCacheEntryDTO | None:
        row = self._conn.execute("SELECT * FROM llm_cache WHERE cache_key = ?", (cache_key,)).fetchone()
        return self._row_to_dto(row, LLMCacheEntryDTO) if row else None

    def save(self, entry: LLMCacheEntryDTO) -> None:
        params = self._dto_to_params(entry)
        cols = ", ".join(params.keys())
        placeholders = ", ".join("?" * len(params))

        def _do():
            self._conn.execute(
                f"INSERT OR REPLACE INTO llm_cache ({cols}) VALUES ({placeholders})",
                tuple(params.values()),
            )

        self._write(_do)

    def increment_hit(self, cache_key: str) -> None:
        def _do():
            self._conn.execute(
                "UPDATE llm_cache SET hit_count = hit_count + 1 WHERE cache_key = ?",
                (cache_key,),
            )

        self._write(_do)
