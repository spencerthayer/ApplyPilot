"""Job repository query/stats methods — extracted from job_repo.py for SRP.

These are read-only reporting methods used by the dashboard, CLI status,
and analytics. Separated from CRUD to keep the core repo under 200 lines.
"""

from __future__ import annotations

from applypilot.db.dto import JobDTO


class JobQueryMixin:
    """Mixin providing read-only query and stats methods for SqliteJobRepository."""

    def get_pipeline_counts(self) -> dict[str, int]:
        """Return pipeline funnel counts for display."""
        queries = {
            "total": "SELECT COUNT(*) FROM jobs",
            "with_desc": "SELECT COUNT(*) FROM jobs WHERE full_description IS NOT NULL",
            "scored": "SELECT COUNT(*) FROM jobs WHERE fit_score IS NOT NULL",
            "tailored": "SELECT COUNT(*) FROM jobs WHERE tailored_resume_path IS NOT NULL",
            "cover_letters": "SELECT COUNT(*) FROM jobs WHERE cover_letter_path IS NOT NULL",
            "ready_to_apply": (
                "SELECT COUNT(*) FROM jobs WHERE tailored_resume_path IS NOT NULL "
                "AND applied_at IS NULL AND application_url IS NOT NULL"
            ),
            "applied": "SELECT COUNT(*) FROM jobs WHERE applied_at IS NOT NULL",
        }
        return {k: self._conn.execute(q).fetchone()[0] for k, q in queries.items()}

    def get_needs_human(self) -> list[JobDTO]:
        rows = self._conn.execute("""
                                  SELECT *
                                  FROM jobs
                                  WHERE apply_status = 'needs_human'
                                  ORDER BY fit_score DESC NULLS LAST, last_attempted_at DESC
                                  """).fetchall()
        return [self._row_to_dto(r, JobDTO) for r in rows]

    def get_stats(self) -> dict:
        """Full pipeline statistics."""
        c = self._conn
        s: dict = {}
        s["total"] = c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        s["by_site"] = [
            (r[0], r[1])
            for r in c.execute("SELECT site, COUNT(*) as cnt FROM jobs GROUP BY site ORDER BY cnt DESC").fetchall()
        ]
        s["pending_detail"] = c.execute("SELECT COUNT(*) FROM jobs WHERE detail_scraped_at IS NULL").fetchone()[0]
        s["with_description"] = c.execute("SELECT COUNT(*) FROM jobs WHERE full_description IS NOT NULL").fetchone()[0]
        s["detail_errors"] = c.execute("SELECT COUNT(*) FROM jobs WHERE detail_error IS NOT NULL").fetchone()[0]
        s["scored"] = c.execute(
            "SELECT COUNT(*) FROM jobs WHERE fit_score IS NOT NULL AND exclusion_rule_id IS NULL"
        ).fetchone()[0]
        s["excluded"] = c.execute("SELECT COUNT(*) FROM jobs WHERE exclusion_rule_id IS NOT NULL").fetchone()[0]
        s["unscored"] = c.execute(
            "SELECT COUNT(*) FROM jobs WHERE full_description IS NOT NULL AND fit_score IS NULL"
        ).fetchone()[0]
        s["score_distribution"] = [
            (r[0], r[1])
            for r in c.execute(
                "SELECT fit_score, COUNT(*) FROM jobs WHERE fit_score IS NOT NULL AND exclusion_rule_id IS NULL "
                "GROUP BY fit_score ORDER BY fit_score DESC"
            ).fetchall()
        ]
        s["tailored"] = c.execute("SELECT COUNT(*) FROM jobs WHERE tailored_resume_path IS NOT NULL").fetchone()[0]
        s["untailored_eligible"] = c.execute(
            "SELECT COUNT(*) FROM jobs WHERE fit_score >= 7 AND full_description IS NOT NULL "
            "AND tailored_resume_path IS NULL"
        ).fetchone()[0]
        s["tailor_exhausted"] = c.execute(
            "SELECT COUNT(*) FROM jobs WHERE COALESCE(tailor_attempts,0) >= 5 AND tailored_resume_path IS NULL"
        ).fetchone()[0]
        s["with_cover_letter"] = c.execute("SELECT COUNT(*) FROM jobs WHERE cover_letter_path IS NOT NULL").fetchone()[
            0
        ]
        s["cover_exhausted"] = c.execute(
            "SELECT COUNT(*) FROM jobs WHERE COALESCE(cover_attempts,0) >= 5 "
            "AND (cover_letter_path IS NULL OR cover_letter_path = '')"
        ).fetchone()[0]
        s["applied"] = c.execute("SELECT COUNT(*) FROM jobs WHERE applied_at IS NOT NULL").fetchone()[0]
        s["apply_errors"] = c.execute("SELECT COUNT(*) FROM jobs WHERE apply_error IS NOT NULL").fetchone()[0]
        s["ready_to_apply"] = c.execute(
            "SELECT COUNT(*) FROM jobs WHERE tailored_resume_path IS NOT NULL "
            "AND applied_at IS NULL AND application_url IS NOT NULL"
        ).fetchone()[0]
        s["needs_human"] = c.execute("SELECT COUNT(*) FROM jobs WHERE apply_status = 'needs_human'").fetchone()[0]

        s["by_category"] = {
            r[0]: {"total": r[1], "10": r[2], "9": r[3], "8": r[4], "7": r[5], "6": r[6], "<6": r[7]}
            for r in c.execute("""
                               SELECT apply_category,
                                      COUNT(*) AS total,
                                      SUM(CASE WHEN fit_score = 10 THEN 1 ELSE 0 END),
                                      SUM(CASE WHEN fit_score = 9 THEN 1 ELSE 0 END),
                                      SUM(CASE WHEN fit_score = 8 THEN 1 ELSE 0 END),
                                      SUM(CASE WHEN fit_score = 7 THEN 1 ELSE 0 END),
                                      SUM(CASE WHEN fit_score = 6 THEN 1 ELSE 0 END),
                                      SUM(CASE WHEN fit_score < 6 OR fit_score IS NULL THEN 1 ELSE 0 END)
                               FROM jobs
                               WHERE apply_category IS NOT NULL
                               GROUP BY apply_category
                               ORDER BY total DESC
                               """).fetchall()
        }

        s["score_funnel"] = [
            {
                "score": r[0],
                "applied": r[1],
                "cover_ready": r[2],
                "tailored": r[3],
                "needs_tailor": r[4],
                "errors": r[5],
            }
            for r in c.execute("""
                               SELECT fit_score,
                                      SUM(CASE WHEN applied_at IS NOT NULL THEN 1 ELSE 0 END),
                                      SUM(CASE
                                              WHEN applied_at IS NULL AND cover_letter_path IS NOT NULL
                                                  AND COALESCE(apply_status, '') NOT IN
                                                      ('applied', 'manual', 'needs_human') THEN 1
                                              ELSE 0 END),
                                      SUM(CASE
                                              WHEN applied_at IS NULL AND tailored_resume_path IS NOT NULL
                                                  AND (cover_letter_path IS NULL OR cover_letter_path = '')
                                                  AND COALESCE(apply_status, '') NOT IN
                                                      ('applied', 'manual', 'needs_human') THEN 1
                                              ELSE 0 END),
                                      SUM(CASE
                                              WHEN applied_at IS NULL AND tailored_resume_path IS NULL
                                                  AND full_description IS NOT NULL
                                                  AND COALESCE(apply_status, '') NOT IN ('applied', 'manual') THEN 1
                                              ELSE 0 END),
                                      SUM(CASE WHEN apply_error IS NOT NULL THEN 1 ELSE 0 END)
                               FROM jobs
                               WHERE fit_score >= 6
                               GROUP BY fit_score
                               ORDER BY fit_score DESC
                               """).fetchall()
        ]
        return s

    def get_score_distribution(self) -> list[tuple[int, int]]:
        rows = self._conn.execute(
            "SELECT fit_score, COUNT(*) FROM jobs WHERE fit_score IS NOT NULL "
            "GROUP BY fit_score ORDER BY fit_score DESC"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def get_dashboard_data(self) -> dict:
        """Return all data needed for the HTML dashboard."""
        c = self._conn
        total = c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        ready = c.execute(
            "SELECT COUNT(*) FROM jobs WHERE full_description IS NOT NULL AND application_url IS NOT NULL"
        ).fetchone()[0]
        scored = c.execute("SELECT COUNT(*) FROM jobs WHERE fit_score IS NOT NULL").fetchone()[0]
        high_fit = c.execute("SELECT COUNT(*) FROM jobs WHERE fit_score >= 7").fetchone()[0]

        score_dist = {
            r[0]: r[1]
            for r in c.execute(
                "SELECT fit_score, COUNT(*) FROM jobs WHERE fit_score IS NOT NULL GROUP BY fit_score ORDER BY fit_score DESC"
            ).fetchall()
        }

        def _rows_to_dicts(rows):
            return [dict(zip(r.keys(), r)) for r in rows] if rows else []

        site_stats = _rows_to_dicts(
            c.execute("""
                      SELECT site,
                             COUNT(*)                                                                 as total,
                             SUM(CASE WHEN fit_score >= 7 THEN 1 ELSE 0 END)                          as high_fit,
                             SUM(CASE WHEN fit_score BETWEEN 5 AND 6 THEN 1 ELSE 0 END)               as mid_fit,
                             SUM(CASE WHEN fit_score < 5 AND fit_score IS NOT NULL THEN 1 ELSE 0 END) as low_fit,
                             SUM(CASE WHEN fit_score IS NULL THEN 1 ELSE 0 END)                       as unscored,
                             ROUND(AVG(fit_score), 1)                                                 as avg_score
                      FROM jobs
                      GROUP BY site
                      ORDER BY high_fit DESC, total DESC
                      """).fetchall()
        )

        jobs = _rows_to_dicts(
            c.execute("""
                      SELECT url,
                             title,
                             salary,
                             description,
                             location,
                             site,
                             strategy,
                             full_description,
                             application_url,
                             detail_error,
                             tailored_resume_path,
                             fit_score,
                             score_reasoning,
                             applied_at,
                             apply_status,
                             apply_error,
                             last_attempted_at
                      FROM jobs
                      WHERE fit_score >= 5
                      ORDER BY fit_score DESC, site, title
                      """).fetchall()
        )

        applied = _rows_to_dicts(
            c.execute("""
                      SELECT url,
                             title,
                             site,
                             location,
                             fit_score,
                             applied_at,
                             apply_duration_ms,
                             application_url
                      FROM jobs
                      WHERE apply_status = 'applied'
                        AND applied_at IS NOT NULL
                      ORDER BY applied_at DESC
                      """).fetchall()
        )

        failed = _rows_to_dicts(
            c.execute("""
                      SELECT url,
                             title,
                             site,
                             location,
                             fit_score,
                             apply_status,
                             apply_error,
                             apply_attempts,
                             last_attempted_at,
                             application_url
                      FROM jobs
                      WHERE apply_status IS NOT NULL
                        AND apply_status != 'applied' AND apply_attempts > 0
                      ORDER BY last_attempted_at DESC
                      """).fetchall()
        )

        return {
            "total": total,
            "ready": ready,
            "scored": scored,
            "high_fit": high_fit,
            "score_dist": score_dist,
            "site_stats": site_stats,
            "jobs": jobs,
            "applied": applied,
            "failed": failed,
        }
