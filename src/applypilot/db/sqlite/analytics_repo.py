"""SqliteAnalyticsRepository — concrete AnalyticsRepository for SQLite."""

from __future__ import annotations

from applypilot.db.dto import AnalyticsEventDTO
from applypilot.db.interfaces.analytics_repository import AnalyticsRepository
from applypilot.db.sqlite.base_repo import SqliteBaseRepo


class SqliteAnalyticsRepository(SqliteBaseRepo, AnalyticsRepository):
    def emit_event(self, event: AnalyticsEventDTO) -> None:
        params = self._dto_to_params(event)
        cols = ", ".join(params.keys())
        placeholders = ", ".join("?" * len(params))

        def _do():
            self._conn.execute(
                f"INSERT INTO analytics_events ({cols}) VALUES ({placeholders})",
                tuple(params.values()),
            )

        self._write(_do)

    def get_unprocessed(self, limit: int = 100) -> list[AnalyticsEventDTO]:
        rows = self._conn.execute(
            "SELECT * FROM analytics_events WHERE processed_at IS NULL ORDER BY timestamp LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_dto(r, AnalyticsEventDTO) for r in rows]

    def mark_processed(self, event_id: str) -> None:
        def _do():
            self._conn.execute(
                "UPDATE analytics_events SET processed_at=datetime('now') WHERE event_id=?",
                (event_id,),
            )

        self._write(_do)

    def get_by_type(self, event_type: str, limit: int = 100) -> list[AnalyticsEventDTO]:
        rows = self._conn.execute(
            "SELECT * FROM analytics_events WHERE event_type = ? ORDER BY timestamp DESC LIMIT ?",
            (event_type, limit),
        ).fetchall()
        return [self._row_to_dto(r, AnalyticsEventDTO) for r in rows]
