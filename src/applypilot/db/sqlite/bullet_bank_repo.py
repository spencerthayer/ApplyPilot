"""SqliteBulletBankRepository — bullet bank persistence via repo pattern."""

from __future__ import annotations

from applypilot.db.dto import BulletBankDTO, BulletFeedbackDTO
from applypilot.db.interfaces.bullet_bank_repository import BulletBankRepository
from applypilot.db.sqlite.base_repo import SqliteBaseRepo


class SqliteBulletBankRepository(SqliteBaseRepo, BulletBankRepository):
    def add_bullet(self, bullet: BulletBankDTO) -> None:
        def _do():
            self._conn.execute(
                "INSERT OR IGNORE INTO bullet_bank "
                "(id, text, context, tags, metrics, created_at, use_count, success_rate) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    bullet.id,
                    bullet.text,
                    bullet.context,
                    bullet.tags,
                    bullet.metrics,
                    bullet.created_at,
                    bullet.use_count,
                    bullet.success_rate,
                ),
            )

        self._write(_do)

    def get_bullet(self, bullet_id: str) -> BulletBankDTO | None:
        row = self._conn.execute("SELECT * FROM bullet_bank WHERE id = ?", (bullet_id,)).fetchone()
        return self._row_to_dto(row, BulletBankDTO) if row else None

    def get_all(self, order_by_success: bool = True) -> list[BulletBankDTO]:
        order = "success_rate DESC" if order_by_success else "created_at DESC"
        rows = self._conn.execute(f"SELECT * FROM bullet_bank ORDER BY {order}").fetchall()
        return [self._row_to_dto(r, BulletBankDTO) for r in rows]

    def record_feedback(self, feedback: BulletFeedbackDTO) -> None:
        def _do():
            self._conn.execute(
                "INSERT INTO bullet_feedback (bullet_id, job_title, outcome, created_at) VALUES (?,?,?,?)",
                (feedback.bullet_id, feedback.job_title, feedback.outcome, feedback.created_at),
            )

        self._write(_do)

    def update_stats(self, bullet_id: str) -> None:
        total = self._conn.execute("SELECT COUNT(*) FROM bullet_feedback WHERE bullet_id = ?", (bullet_id,)).fetchone()[
            0
        ]
        successes = self._conn.execute(
            "SELECT COUNT(*) FROM bullet_feedback WHERE bullet_id = ? AND outcome = 'selected'",
            (bullet_id,),
        ).fetchone()[0]
        rate = successes / total if total > 0 else 0.0

        def _do():
            self._conn.execute(
                "UPDATE bullet_bank SET use_count = use_count + 1, success_rate = ? WHERE id = ?",
                (rate, bullet_id),
            )

        self._write(_do)
