"""Job repository apply methods — extracted from job_repo.py for SRP.

Apply-specific: acquire, lock, park, classify, reset.
"""

from __future__ import annotations

from applypilot.db.dto import JobDTO


class JobApplyMixin:
    """Mixin providing apply-specific methods for SqliteJobRepository."""

    def acquire_next_filtered(
            self,
            min_score: int,
            max_attempts: int,
            agent_id: str,
            blocked_sites: list[str] | None = None,
            blocked_patterns: list[str] | None = None,
    ) -> JobDTO | None:
        params: list = [max_attempts, min_score]
        site_clause = ""
        if blocked_sites:
            placeholders = ",".join("?" * len(blocked_sites))
            site_clause = f"AND site NOT IN ({placeholders})"
            params.extend(blocked_sites)
        url_clauses = ""
        if blocked_patterns:
            url_clauses = " ".join("AND url NOT LIKE ?" for _ in blocked_patterns)
            params.extend(blocked_patterns)

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            # Round-robin: rotate across boards (strategy) and companies (site).
            # Pick the job from the least-recently-applied board+company combo.
            # This prevents spamming one employer and spreads across ATS types.
            # Round-robin: one job per company, rotate across all companies.
            # ROW_NUMBER partitions by company — pick row 1 from each company first,
            # then row 2, etc. Within each round, least-recently-applied company goes first.
            row = self._conn.execute(
                f"""
                WITH ranked AS (
                    SELECT j.*,
                        ROW_NUMBER() OVER (PARTITION BY j.site ORDER BY j.fit_score DESC) as rn,
                        recent.last_apply as _last_apply
                    FROM jobs j
                    LEFT JOIN (
                        SELECT site as _rsite, MAX(last_attempted_at) as last_apply
                        FROM jobs WHERE apply_status IN ('applied', 'in_progress', 'needs_human')
                        GROUP BY site
                    ) recent ON j.site = recent._rsite
                    WHERE j.tailored_resume_path IS NOT NULL
                      AND (j.apply_status IS NULL OR j.apply_status = 'failed')
                      AND (j.apply_attempts IS NULL OR j.apply_attempts < ?)
                      AND j.fit_score >= ?
                      {site_clause} {url_clauses}
                )
                SELECT * FROM ranked
                ORDER BY rn, _last_apply ASC NULLS FIRST, fit_score DESC
                LIMIT 1
            """,
                params,
            ).fetchone()
            if not row:
                self._conn.rollback()
                return None
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "UPDATE jobs SET apply_status='in_progress', agent_id=?, last_attempted_at=? WHERE url=?",
                (agent_id, now, row["url"]),
            )
            self._conn.commit()
            return self._row_to_dto(row, JobDTO)
        except Exception:
            self._conn.rollback()
            raise

    def get_target_job(self, url: str) -> JobDTO | None:
        like = f"%{url.split('?')[0].rstrip('/')}%"
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                """
                SELECT *
                FROM jobs
                WHERE (url = ? OR application_url = ? OR application_url LIKE ? OR url LIKE ?)
                  AND tailored_resume_path IS NOT NULL
                  AND (apply_status IS NULL OR apply_status != 'in_progress') LIMIT 1
                """,
                (url, url, like, like),
            ).fetchone()
            if not row:
                self._conn.rollback()
                return None
            return self._row_to_dto(row, JobDTO)
        except Exception:
            self._conn.rollback()
            raise

    def lock_for_apply(self, url: str, agent_id: str) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE jobs SET apply_status='in_progress', agent_id=?, last_attempted_at=? WHERE url=?",
            (agent_id, now, url),
        )
        self._conn.commit()

    def park_for_human_review(self, url: str, reason: str, apply_url: str, instructions: str) -> None:
        def _do():
            self._conn.execute(
                "UPDATE jobs SET apply_status='needs_human', needs_human_reason=?, "
                "needs_human_url=?, needs_human_instructions=? WHERE url=?",
                (reason, apply_url, instructions, url),
            )

        self._write(_do)
        self._conn.commit()

    def mark_permanent_failure(self, url: str) -> None:
        def _do():
            self._conn.execute("UPDATE jobs SET apply_attempts=99 WHERE url=?", (url,))

        self._write(_do)
        self._conn.commit()

    def get_priority_queue(self, limit: int = 50) -> list[JobDTO]:
        rows = self._conn.execute(
            "SELECT *, fit_score * COALESCE(tier_weight, 0.7) AS priority, "
            "ROW_NUMBER() OVER (PARTITION BY COALESCE(company, site) ORDER BY fit_score DESC) AS company_rank "
            "FROM jobs WHERE apply_status IS NULL AND tailored_resume_path IS NOT NULL "
            "ORDER BY company_rank ASC, priority DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_dto(r, JobDTO) for r in rows]

    def reset_stale_in_progress(self, timeout_minutes: int = 5) -> int:
        cur = self._conn.execute(
            "UPDATE jobs SET apply_status=NULL, agent_id=NULL "
            "WHERE apply_status='in_progress' AND last_attempted_at < datetime('now', ?)",
            (f"-{timeout_minutes} minutes",),
        )
        self._conn.commit()
        return cur.rowcount

    def reset_failed_jobs(self) -> int:
        cur = self._conn.execute(
            "UPDATE jobs SET apply_status=NULL, apply_error=NULL, apply_attempts=0, agent_id=NULL "
            "WHERE apply_status='failed' OR (apply_status IS NOT NULL AND apply_status != 'applied' "
            "AND apply_status != 'in_progress')"
        )
        self._conn.commit()
        return cur.rowcount
