"""SqliteJobRepository — concrete JobRepository for SQLite.

Core CRUD + stage queries. Stats/reporting in job_queries.py, apply logic in job_apply.py.
"""

from __future__ import annotations

from applypilot.db.dto import (
    ApplyResultDTO,
    CoverLetterResultDTO,
    EnrichErrorDTO,
    EnrichResultDTO,
    ExclusionResultDTO,
    JobDTO,
    ScoreFailureDTO,
    ScoreResultDTO,
    TailorResultDTO,
)
from applypilot.db.interfaces.job_repository import JobRepository
from applypilot.db.sqlite.base_repo import SqliteBaseRepo
from applypilot.db.sqlite.job_queries import JobQueryMixin
from applypilot.db.sqlite.job_apply import JobApplyMixin

_STAGE_FILTERS = {
    "pending_enrichment": "full_description IS NULL AND detail_error IS NULL",
    "pending_scoring": (
        "full_description IS NOT NULL AND fit_score IS NULL AND exclusion_reason_code IS NULL AND score_error IS NULL"
    ),
    "pending_tailoring": (
        "fit_score IS NOT NULL AND fit_score > 0 AND tailored_resume_path IS NULL AND exclusion_reason_code IS NULL"
    ),
    "pending_cover": "tailored_resume_path IS NOT NULL AND cover_letter_path IS NULL",
    "pending_apply": "tailored_resume_path IS NOT NULL AND apply_status IS NULL",
}


class SqliteJobRepository(SqliteBaseRepo, JobQueryMixin, JobApplyMixin, JobRepository):
    # ── Core CRUD ───────────────────────────────────────────────────────

    def upsert(self, job: JobDTO) -> None:
        params = self._dto_to_params(job)
        if "pipeline_status" not in params or not params.get("pipeline_status"):
            params["pipeline_status"] = "discovered"
        cols = ", ".join(params.keys())
        placeholders = ", ".join("?" * len(params))

        def _do():
            self._conn.execute(f"INSERT OR REPLACE INTO jobs ({cols}) VALUES ({placeholders})", tuple(params.values()))

        self._write(_do)

    def get_by_url(self, url: str) -> JobDTO | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()
        return self._row_to_dto(row, JobDTO) if row else None

    def get_by_stage(self, stage: str, limit: int = 0) -> list[JobDTO]:
        where = _STAGE_FILTERS.get(stage, "1=1")
        sql = f"SELECT * FROM jobs WHERE {where} ORDER BY fit_score DESC"
        if limit:
            sql += f" LIMIT {limit}"
        return [self._row_to_dto(r, JobDTO) for r in self._conn.execute(sql).fetchall()]

    # ── Pipeline updates ────────────────────────────────────────────────

    def update_enrichment(self, result: EnrichResultDTO) -> None:
        def _do():
            self._conn.execute(
                "UPDATE jobs SET full_description=?, application_url=?, detail_scraped_at=?, "
                "detail_error=NULL, detail_error_category=NULL, detail_retry_count=0, "
                "detail_next_retry_at=NULL, pipeline_status='enriched' WHERE url=?",
                (result.full_description, result.application_url, result.detail_scraped_at, result.url),
            )

        self._write(_do)

    def update_enrichment_error(self, result: EnrichErrorDTO) -> None:
        def _do():
            self._conn.execute(
                "UPDATE jobs SET detail_error=?, detail_error_category=?, detail_retry_count=?, "
                "detail_next_retry_at=?, detail_scraped_at=?, pipeline_status='enrichment_failed' WHERE url=?",
                (
                    result.detail_error,
                    result.detail_error_category,
                    result.detail_retry_count,
                    result.detail_next_retry_at,
                    result.detail_scraped_at,
                    result.url,
                ),
            )

        self._write(_do)

    def update_score(self, result: ScoreResultDTO) -> None:
        def _do():
            self._conn.execute(
                "UPDATE jobs SET fit_score=?, score_reasoning=?, scored_at=?, "
                "exclusion_reason_code=NULL, exclusion_rule_id=NULL, excluded_at=NULL, "
                "score_error=NULL, score_retry_count=0, score_next_retry_at=NULL, "
                "pipeline_status='scored' WHERE url=?",
                (result.fit_score, result.score_reasoning, result.scored_at, result.url),
            )

        self._write(_do)

    def update_exclusion(self, result: ExclusionResultDTO) -> None:
        def _do():
            self._conn.execute(
                "UPDATE jobs SET fit_score=0, score_reasoning=?, scored_at=?, "
                "exclusion_reason_code=?, exclusion_rule_id=?, excluded_at=?, "
                "score_error=NULL, score_retry_count=0, score_next_retry_at=NULL, "
                "pipeline_status='excluded' WHERE url=?",
                (
                    result.score_reasoning,
                    result.scored_at,
                    result.exclusion_reason_code,
                    result.exclusion_rule_id,
                    result.scored_at,
                    result.url,
                ),
            )

        self._write(_do)

    def update_score_failure(self, result: ScoreFailureDTO) -> None:
        def _do():
            self._conn.execute(
                "UPDATE jobs SET fit_score=NULL, score_reasoning=?, scored_at=NULL, "
                "exclusion_reason_code=NULL, exclusion_rule_id=NULL, excluded_at=NULL, "
                "score_error=?, score_retry_count=?, score_next_retry_at=? WHERE url=?",
                (
                    result.score_reasoning,
                    result.score_error,
                    result.score_retry_count,
                    result.score_next_retry_at,
                    result.url,
                ),
            )

        self._write(_do)

    def update_tailoring(self, result: TailorResultDTO) -> None:
        def _do():
            self._conn.execute(
                "UPDATE jobs SET tailored_resume_path=?, tailored_at=?, pipeline_status='tailored' WHERE url=?",
                (result.tailored_resume_path, result.tailored_at, result.url),
            )

        self._write(_do)

    def update_cover_letter(self, result: CoverLetterResultDTO) -> None:
        def _do():
            self._conn.execute(
                "UPDATE jobs SET cover_letter_path=?, cover_letter_at=?, pipeline_status='ready' WHERE url=?",
                (result.cover_letter_path, result.cover_letter_at, result.url),
            )

        self._write(_do)

    def update_apply_status(self, result: ApplyResultDTO) -> None:
        _map = {"applied": "applied", "failed": "failed", "needs_human": "needs_human", "in_progress": "in_progress"}
        ps = _map.get(result.apply_status, result.apply_status)

        def _do():
            self._conn.execute(
                "UPDATE jobs SET apply_status=?, apply_error=?, applied_at=?, "
                "apply_duration_ms=?, agent_id=NULL, pipeline_status=? WHERE url=?",
                (result.apply_status, result.apply_error, result.applied_at, result.apply_duration_ms, ps, result.url),
            )

        self._write(_do)

    # ── Utility ─────────────────────────────────────────────────────────

    def acquire_next(self, min_score: int, max_attempts: int, agent_id: str) -> JobDTO | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE tailored_resume_path IS NOT NULL "
            "AND (apply_status IS NULL OR apply_status='failed') "
            "AND apply_attempts < ? AND fit_score >= ? ORDER BY fit_score DESC LIMIT 1",
            (max_attempts, min_score),
        ).fetchone()
        if not row:
            return None
        self._conn.execute(
            "UPDATE jobs SET apply_status='in_progress', agent_id=?, last_attempted_at=datetime('now') WHERE url=?",
            (agent_id, row["url"]),
        )
        self._conn.commit()
        return self._row_to_dto(row, JobDTO)

    def count_by_status(self) -> dict[str, int]:
        rows = self._conn.execute("SELECT apply_status, COUNT(*) FROM jobs GROUP BY apply_status").fetchall()
        return {(r[0] or "pending"): r[1] for r in rows}

    def update_url(self, old_url: str, new_url: str) -> bool:
        try:

            def _do():
                self._conn.execute("UPDATE jobs SET url=? WHERE url=?", (new_url, old_url))

            self._write(_do)
            return True
        except Exception:
            return False

    def delete(self, url: str) -> None:
        def _do():
            self._conn.execute("DELETE FROM jobs WHERE url=?", (url,))

        self._write(_do)

    def update_application_url(self, url: str, application_url: str) -> None:
        def _do():
            self._conn.execute("UPDATE jobs SET application_url=? WHERE url=?", (application_url, url))

        self._write(_do)

    def increment_attempts(self, url: str, column: str) -> None:
        allowed = {"tailor_attempts", "cover_attempts", "apply_attempts"}
        if column not in allowed:
            raise ValueError(f"Invalid attempts column: {column}")

        def _do():
            self._conn.execute(f"UPDATE jobs SET {column}=COALESCE({column},0)+1 WHERE url=?", (url,))

        self._write(_do)

    def commit(self) -> None:
        self._conn.commit()

    def find_by_url_fuzzy(self, url: str) -> JobDTO | None:
        like = f"%{url.split('?')[0].rstrip('/')}%"
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE (url=? OR application_url=? OR application_url LIKE ? OR url LIKE ?) LIMIT 1",
            (url, url, like, like),
        ).fetchone()
        return self._row_to_dto(row, JobDTO) if row else None

    def get_by_rowid(self, rowid: int) -> JobDTO | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE rowid=? LIMIT 1", (rowid,)).fetchone()
        return self._row_to_dto(row, JobDTO) if row else None

    def get_all_urls_and_sites(self) -> list[tuple[str, str]]:
        return [(r[0], r[1]) for r in self._conn.execute("SELECT url, site FROM jobs").fetchall()]

    def get_relative_application_urls(self) -> list[tuple[str, str, str]]:
        return [
            (r[0], r[1], r[2])
            for r in self._conn.execute(
                "SELECT url, site, application_url FROM jobs "
                "WHERE application_url IS NOT NULL AND application_url != '' AND application_url NOT LIKE 'http%'"
            ).fetchall()
        ]

    def get_wttj_jobs(self) -> list[tuple[str, str]]:
        return [
            (r[0], r[1])
            for r in self._conn.execute("SELECT url, title FROM jobs WHERE site='WelcomeToTheJungle'").fetchall()
        ]

    def get_wttj_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM jobs WHERE site='WelcomeToTheJungle'").fetchone()[0]

    def get_wttj_sample_url(self) -> str | None:
        row = self._conn.execute("SELECT url FROM jobs WHERE site='WelcomeToTheJungle' LIMIT 1").fetchone()
        return row[0] if row else None

    def get_pending_enrichment(self, skip_sites: list[str], job_url: str | None = None) -> list[JobDTO]:
        placeholders = ",".join("?" * len(skip_sites))
        where = (
            f"site NOT IN ({placeholders}) AND (detail_scraped_at IS NULL "
            "OR (detail_error_category='retriable' AND (detail_next_retry_at IS NULL OR detail_next_retry_at <= datetime('now'))))"
        )
        params: list = list(skip_sites)
        if job_url:
            where += " AND url=?"
            params.append(job_url)
        return [
            self._row_to_dto(r, JobDTO)
            for r in self._conn.execute(
                f"SELECT * FROM jobs WHERE {where} ORDER BY site, discovered_at DESC", params
            ).fetchall()
        ]

    def get_detail_retry_count(self, url: str) -> int:
        row = self._conn.execute("SELECT COALESCE(detail_retry_count,0) FROM jobs WHERE url=?", (url,)).fetchone()
        return row[0] if row else 0

    def autoheal_legacy_llm_failures(self, error_pattern: str) -> int:
        rows = self._conn.execute(
            "SELECT url, score_reasoning, COALESCE(score_retry_count,0) FROM jobs "
            "WHERE fit_score=0 AND COALESCE(exclusion_reason_code,'')='' AND COALESCE(exclusion_rule_id,'')='' "
            "AND COALESCE(score_reasoning,'') LIKE ?",
            (error_pattern,),
        ).fetchall()
        if not rows:
            return 0
        for row in rows:
            reasoning = (row[1] or "").strip()
            error = reasoning if reasoning.lower().startswith("llm error:") else f"LLM error: {reasoning}"
            self._conn.execute(
                "UPDATE jobs SET fit_score=NULL, score_reasoning=NULL, scored_at=NULL, "
                "score_error=?, score_retry_count=?, score_next_retry_at=NULL, "
                "exclusion_reason_code=NULL, exclusion_rule_id=NULL, excluded_at=NULL WHERE url=?",
                (error, max(int(row[2] or 0), 1), row[0]),
            )
        self._conn.commit()
        return len(rows)

    # ── Pipeline status ─────────────────────────────────────────────────

    def get_by_pipeline_status(self, status: str, limit: int = 0) -> list[JobDTO]:
        sql = "SELECT * FROM jobs WHERE pipeline_status=? ORDER BY fit_score DESC NULLS LAST"
        if limit:
            sql += f" LIMIT {limit}"
        return [self._row_to_dto(r, JobDTO) for r in self._conn.execute(sql, (status,)).fetchall()]

    def set_pipeline_status(self, url: str, status: str) -> None:
        def _do():
            self._conn.execute("UPDATE jobs SET pipeline_status=? WHERE url=?", (status, url))

        self._write(_do)

    def backfill_pipeline_status(self) -> int:
        rows = self._conn.execute(
            "SELECT url, full_description, fit_score, exclusion_reason_code, "
            "tailored_resume_path, cover_letter_path, apply_status, applied_at "
            "FROM jobs WHERE pipeline_status IS NULL"
        ).fetchall()
        for r in rows:
            match (
                r["applied_at"],
                r["apply_status"],
                r["cover_letter_path"],
                r["tailored_resume_path"],
                r["exclusion_reason_code"],
                r["fit_score"],
                r["full_description"],
            ):
                case (a, _, _, _, _, _, _) if a:
                    status = "applied"
                case (_, "needs_human", _, _, _, _, _):
                    status = "needs_human"
                case (_, "in_progress", _, _, _, _, _):
                    status = "in_progress"
                case (_, "failed", _, _, _, _, _):
                    status = "failed"
                case (_, _, cl, _, _, _, _) if cl:
                    status = "ready"
                case (_, _, _, tr, _, _, _) if tr:
                    status = "tailored"
                case (_, _, _, _, exc, _, _) if exc:
                    status = "excluded"
                case (_, _, _, _, _, s, _) if s is not None:
                    status = "scored"
                case (_, _, _, _, _, _, d) if d:
                    status = "enriched"
                case _:
                    status = "discovered"
            self._conn.execute("UPDATE jobs SET pipeline_status=? WHERE url=?", (status, r["url"]))
        if rows:
            self._conn.commit()
        return len(rows)

    # ── Stage queries (advanced filtering) ─────────────────────────────

    def get_jobs_by_stage_dict(
            self, stage: str, *, min_score: int | None = None, limit: int = 100, job_url: str | None = None
    ) -> list[JobDTO]:
        conditions = {
            "discovered": "1=1",
            "pending_detail": (
                "detail_scraped_at IS NULL OR (detail_error_category='retriable' "
                "AND (detail_next_retry_at IS NULL OR detail_next_retry_at <= datetime('now')))"
            ),
            "enriched": "full_description IS NOT NULL",
            "pending_score": (
                "full_description IS NOT NULL AND ((fit_score IS NULL AND score_error IS NULL) "
                "OR (score_error IS NOT NULL AND (score_next_retry_at IS NULL OR score_next_retry_at <= datetime('now'))))"
            ),
            "scored": "fit_score IS NOT NULL",
            "pending_tailor": (
                "fit_score >= ? AND full_description IS NOT NULL "
                "AND tailored_resume_path IS NULL AND COALESCE(tailor_attempts,0) < 5"
            ),
            "tailored": "tailored_resume_path IS NOT NULL",
            "pending_cover": (
                "fit_score >= ? AND tailored_resume_path IS NOT NULL AND full_description IS NOT NULL "
                "AND (cover_letter_path IS NULL OR cover_letter_path='') AND COALESCE(cover_attempts,0) < 5"
            ),
            "pending_apply": "tailored_resume_path IS NOT NULL AND applied_at IS NULL AND application_url IS NOT NULL",
            "applied": "applied_at IS NOT NULL",
        }
        where = conditions.get(stage, "1=1")
        params: list = []
        if "?" in where:
            params.append(min_score if min_score is not None else 7)
        if min_score is not None and "fit_score" not in where and stage in ("scored", "tailored", "applied"):
            where += " AND fit_score >= ?"
            params.append(min_score)
        if job_url is not None:
            where += " AND url=?"
            params.append(job_url)
        query = f"""SELECT * FROM (SELECT *, ROW_NUMBER() OVER (
            PARTITION BY COALESCE(site,'unknown') ORDER BY discovered_at DESC) AS _site_rank
            FROM jobs WHERE {where}) ORDER BY fit_score DESC NULLS LAST, _site_rank ASC, discovered_at DESC"""
        if limit > 0:
            query += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_dto(r, JobDTO) for r in rows]

    def find_by_tailored_path(self, path: str) -> list[JobDTO]:
        rows = self._conn.execute("SELECT * FROM jobs WHERE tailored_resume_path = ?", (path,)).fetchall()
        return [self._row_to_dto(r, JobDTO) for r in rows]

    def clear_tailoring(self, url: str) -> None:
        def _do():
            self._conn.execute(
                "UPDATE jobs SET tailored_resume_path=NULL, tailored_at=NULL, "
                "cover_letter_path=NULL, cover_letter_at=NULL WHERE url=?",
                (url,),
            )

        self._write(_do)

    def search_fts(self, query: str, limit: int = 50) -> list[JobDTO]:
        try:
            rows = self._conn.execute(
                "SELECT j.* FROM jobs j JOIN jobs_fts f ON j.rowid = f.rowid "
                "WHERE jobs_fts MATCH ? ORDER BY rank LIMIT ?",
                (query, limit),
            ).fetchall()
            return [self._row_to_dto(r, JobDTO) for r in rows]
        except Exception:
            return []

    def update_job_fields_generic(self, url: str, fields: dict) -> None:
        if not fields:
            return
        set_clauses = ", ".join(f"{k}=?" for k in fields)
        values = list(fields.values()) + [url]

        def _do():
            self._conn.execute(f"UPDATE jobs SET {set_clauses} WHERE url=?", values)

        self._write(_do)
