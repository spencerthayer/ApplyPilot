"""SqliteTrackRepository — concrete TrackRepository for SQLite."""

from __future__ import annotations

from applypilot.db.dto import TrackMappingDTO
from applypilot.db.interfaces.track_repository import TrackRepository
from applypilot.db.sqlite.base_repo import SqliteBaseRepo


class SqliteTrackRepository(SqliteBaseRepo, TrackRepository):
    def save_mapping(self, mapping: TrackMappingDTO) -> None:
        params = self._dto_to_params(mapping)
        cols = ", ".join(params.keys())
        placeholders = ", ".join("?" * len(params))

        def _do():
            self._conn.execute(
                f"INSERT OR REPLACE INTO track_piece_mappings ({cols}) VALUES ({placeholders})",
                tuple(params.values()),
            )

        self._write(_do)

    def get_mappings(self, track_id: str) -> list[TrackMappingDTO]:
        rows = self._conn.execute("SELECT * FROM track_piece_mappings WHERE track_id = ?", (track_id,)).fetchall()
        return [self._row_to_dto(r, TrackMappingDTO) for r in rows]

    def delete_track(self, track_id: str) -> int:
        cur = self._conn.execute("DELETE FROM track_piece_mappings WHERE track_id = ?", (track_id,))
        self._conn.execute("DELETE FROM tracks WHERE track_id = ?", (track_id,))
        self._conn.commit()
        return cur.rowcount

    def save(self, track_id: str, name: str, skills: list[str], active: bool) -> None:
        import json
        from datetime import datetime, timezone

        def _do():
            self._conn.execute(
                "INSERT OR REPLACE INTO tracks (track_id, name, skills, active, created_at) VALUES (?,?,?,?,?)",
                (track_id, name, json.dumps(skills), int(active), datetime.now(timezone.utc).isoformat()),
            )

        self._write(_do)

    def get_all_tracks(self) -> list[dict]:
        import json

        rows = self._conn.execute("SELECT * FROM tracks ORDER BY name").fetchall()
        result = []
        for r in rows:
            result.append(
                {
                    "track_id": r["track_id"],
                    "name": r["name"],
                    "skills": json.loads(r["skills"]) if r["skills"] else [],
                    "active": bool(r["active"]),
                    "base_resume_path": r["base_resume_path"],
                }
            )
        return result

    def set_active(self, track_id: str, active: bool) -> None:
        def _do():
            self._conn.execute("UPDATE tracks SET active=? WHERE track_id=?", (int(active), track_id))

        self._write(_do)

    def update_base_resume_path(self, track_id: str, path: str) -> None:
        def _do():
            self._conn.execute("UPDATE tracks SET base_resume_path=? WHERE track_id=?", (path, track_id))

        self._write(_do)
