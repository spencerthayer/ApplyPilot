"""SqlitePieceRepository — concrete PieceRepository for SQLite."""

from __future__ import annotations

from applypilot.db.dto import PieceDTO
from applypilot.db.interfaces.piece_repository import PieceRepository
from applypilot.db.sqlite.base_repo import SqliteBaseRepo


class SqlitePieceRepository(SqliteBaseRepo, PieceRepository):
    def save(self, piece: PieceDTO) -> None:
        params = self._dto_to_params(piece)
        cols = ", ".join(params.keys())
        placeholders = ", ".join("?" * len(params))

        def _do():
            self._conn.execute(
                f"INSERT OR REPLACE INTO pieces ({cols}) VALUES ({placeholders})",
                tuple(params.values()),
            )

        self._write(_do)

    def save_many(self, pieces: list[PieceDTO]) -> None:
        if not pieces:
            return

        def _do():
            for p in pieces:
                params = self._dto_to_params(p)
                cols = ", ".join(params.keys())
                placeholders = ", ".join("?" * len(params))
                self._conn.execute(
                    f"INSERT OR REPLACE INTO pieces ({cols}) VALUES ({placeholders})",
                    tuple(params.values()),
                )

        self._write(_do)

    def get_by_id(self, piece_id: str) -> PieceDTO | None:
        row = self._conn.execute("SELECT * FROM pieces WHERE id = ?", (piece_id,)).fetchone()
        return self._row_to_dto(row, PieceDTO) if row else None

    def get_by_hash(self, content_hash: str) -> PieceDTO | None:
        row = self._conn.execute("SELECT * FROM pieces WHERE content_hash = ?", (content_hash,)).fetchone()
        return self._row_to_dto(row, PieceDTO) if row else None

    def get_by_type(self, piece_type: str) -> list[PieceDTO]:
        rows = self._conn.execute(
            "SELECT * FROM pieces WHERE piece_type = ? ORDER BY sort_order",
            (piece_type,),
        ).fetchall()
        return [self._row_to_dto(r, PieceDTO) for r in rows]

    def get_children(self, parent_id: str) -> list[PieceDTO]:
        rows = self._conn.execute(
            "SELECT * FROM pieces WHERE parent_piece_id = ? ORDER BY sort_order",
            (parent_id,),
        ).fetchall()
        return [self._row_to_dto(r, PieceDTO) for r in rows]

    def get_track_pieces(self, track_id: str) -> list[PieceDTO]:
        rows = self._conn.execute(
            "SELECT p.* FROM pieces p "
            "JOIN track_piece_mappings m ON p.id = m.piece_id "
            "WHERE m.track_id = ? AND m.include = 1 "
            "ORDER BY COALESCE(m.sort_override, p.sort_order)",
            (track_id,),
        ).fetchall()
        return [self._row_to_dto(r, PieceDTO) for r in rows]
