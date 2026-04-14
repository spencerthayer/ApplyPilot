"""SqliteTrackingRepository."""

from __future__ import annotations

from datetime import datetime, timezone

from applypilot.db.dto import JobDTO, TrackingEmailDTO, TrackingPersonDTO
from applypilot.db.interfaces.tracking_repository import TrackingRepository
from applypilot.db.sqlite.base_repo import SqliteBaseRepo

_TRACKING_PRIORITY = {
    "ghosted": 1,
    "rejection": 2,
    "confirmation": 3,
    "follow_up": 4,
    "interview": 5,
    "offer": 6,
}


class SqliteTrackingRepository(SqliteBaseRepo, TrackingRepository):
    def get_applied_jobs(self) -> list[JobDTO]:
        rows = self._conn.execute("SELECT * FROM jobs WHERE applied_at IS NOT NULL ORDER BY applied_at DESC").fetchall()
        return [self._row_to_dto(r, JobDTO) for r in rows]

    def update_tracking_status(self, job_url: str, new_status: str) -> bool:
        row = self._conn.execute("SELECT tracking_status FROM jobs WHERE url = ?", (job_url,)).fetchone()
        if row is None:
            return False
        current_pri = _TRACKING_PRIORITY.get(row["tracking_status"], 0)
        new_pri = _TRACKING_PRIORITY.get(new_status, 0)
        if new_pri > current_pri:
            now = datetime.now(timezone.utc).isoformat()

            def _do():
                self._conn.execute(
                    "UPDATE jobs SET tracking_status=?, tracking_updated_at=? WHERE url=?", (new_status, now, job_url)
                )

            self._write(_do)
            return True
        return False

    def get_emails(self, job_url: str) -> list[TrackingEmailDTO]:
        rows = self._conn.execute(
            "SELECT * FROM tracking_emails WHERE job_url=? ORDER BY received_at ASC", (job_url,)
        ).fetchall()
        return [self._row_to_dto(r, TrackingEmailDTO) for r in rows]

    def get_people(self, job_url: str) -> list[TrackingPersonDTO]:
        rows = self._conn.execute(
            "SELECT * FROM tracking_people WHERE job_url=? ORDER BY first_seen_at ASC", (job_url,)
        ).fetchall()
        return [self._row_to_dto(r, TrackingPersonDTO) for r in rows]

    def get_action_items(self) -> list[JobDTO]:
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE next_action IS NOT NULL "
            "ORDER BY CASE WHEN next_action_due IS NULL THEN 1 ELSE 0 END, next_action_due ASC"
        ).fetchall()
        return [self._row_to_dto(r, JobDTO) for r in rows]

    def get_stats(self) -> dict:
        rows = self._conn.execute(
            "SELECT tracking_status, COUNT(*) as cnt FROM jobs "
            "WHERE tracking_status IS NOT NULL GROUP BY tracking_status ORDER BY cnt DESC"
        ).fetchall()
        return {r["tracking_status"]: r["cnt"] for r in rows}

    def store_email(self, email: TrackingEmailDTO) -> None:
        def _do():
            self._conn.execute(
                "INSERT OR IGNORE INTO tracking_emails "
                "(email_id, thread_id, job_url, sender, sender_name, subject, "
                "received_at, snippet, body_text, classification, extracted_data, classified_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    email.email_id,
                    email.thread_id,
                    email.job_url,
                    email.sender,
                    email.sender_name,
                    email.subject,
                    email.received_at,
                    email.snippet,
                    (email.body_text or "")[:10000],
                    email.classification,
                    email.extracted_data,
                    email.classified_at,
                ),
            )

        self._write(_do)

    def store_person(self, person: TrackingPersonDTO) -> None:
        def _do():
            self._conn.execute(
                "INSERT OR IGNORE INTO tracking_people "
                "(job_url, name, title, email, source_email_id, first_seen_at) "
                "VALUES (?,?,?,?,?,?)",
                (person.job_url, person.name, person.title, person.email, person.source_email_id, person.first_seen_at),
            )

        self._write(_do)

    def email_exists(self, email_id: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM tracking_emails WHERE email_id=?", (email_id,)).fetchone()
        return row is not None

    def update_job_fields(self, job_url: str, fields: dict) -> None:
        if not fields:
            return
        set_clauses = ", ".join(f"{k}=?" for k in fields)
        values = list(fields.values()) + [job_url]

        def _do():
            self._conn.execute(f"UPDATE jobs SET {set_clauses} WHERE url=?", values)

        self._write(_do)

    def get_multi_email_stub_urls(self) -> list[tuple[str, int]]:
        rows = self._conn.execute("""
                                  SELECT job_url, COUNT(*) AS email_count
                                  FROM tracking_emails
                                  WHERE job_url LIKE 'manual://%'
                                  GROUP BY job_url
                                  HAVING COUNT(*) > 1
                                  """).fetchall()
        return [(r["job_url"], r["email_count"]) for r in rows]

    def get_stub_email_dicts(self, job_url: str) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT email_id,
                   sender,
                   sender_name,
                   subject,
                   snippet,
                   received_at,
                   classification,
                   body_text
            FROM tracking_emails
            WHERE job_url = ?
            """,
            (job_url,),
        ).fetchall()
        return [dict(zip(r.keys(), r)) for r in rows]

    def move_email_to_job(self, email_id: str, new_job_url: str) -> None:
        def _do():
            self._conn.execute(
                "UPDATE tracking_emails SET job_url = ? WHERE email_id = ?",
                (new_job_url, email_id),
            )

        self._write(_do)

    def delete_orphan_stubs(self) -> int:
        rows = self._conn.execute("""
                                  SELECT url
                                  FROM jobs
                                  WHERE url LIKE 'manual://%'
                                    AND url NOT IN (SELECT DISTINCT job_url FROM tracking_emails)
                                  """).fetchall()
        count = 0
        for row in rows:
            def _do(u=row["url"]):
                self._conn.execute("DELETE FROM jobs WHERE url = ?", (u,))

            self._write(_do)
            count += 1
        return count

    def get_all_email_ids(self) -> list[str]:
        rows = self._conn.execute("SELECT email_id FROM tracking_emails").fetchall()
        return [r["email_id"] for r in rows]
