"""SqliteOverlayRepository — concrete OverlayRepository for SQLite."""

from __future__ import annotations

from applypilot.db.dto import OverlayDTO
from applypilot.db.interfaces.overlay_repository import OverlayRepository
from applypilot.db.sqlite.base_repo import SqliteBaseRepo


class SqliteOverlayRepository(SqliteBaseRepo, OverlayRepository):
    def save(self, overlay: OverlayDTO) -> None:
        params = self._dto_to_params(overlay)
        cols = ", ".join(params.keys())
        placeholders = ", ".join("?" * len(params))

        def _do():
            self._conn.execute(
                f"INSERT OR REPLACE INTO overlays ({cols}) VALUES ({placeholders})",
                tuple(params.values()),
            )

        self._write(_do)

    def get_for_job(self, job_url: str, track_id: str | None = None) -> list[OverlayDTO]:
        if track_id:
            rows = self._conn.execute(
                "SELECT * FROM overlays WHERE job_url = ? AND track_id = ?",
                (job_url, track_id),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM overlays WHERE job_url = ?", (job_url,)).fetchall()
        return [self._row_to_dto(r, OverlayDTO) for r in rows]

    def get_for_piece(self, piece_id: str) -> list[OverlayDTO]:
        rows = self._conn.execute("SELECT * FROM overlays WHERE piece_id = ?", (piece_id,)).fetchall()
        return [self._row_to_dto(r, OverlayDTO) for r in rows]
