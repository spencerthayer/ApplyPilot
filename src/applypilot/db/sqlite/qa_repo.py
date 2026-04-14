"""SqliteQARepository."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from applypilot.db.interfaces.qa_repository import QARepository
from applypilot.db.sqlite.base_repo import SqliteBaseRepo
from applypilot.db.dto import QAKnowledgeDTO  # noqa: F811


def _normalize_question(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text)


def _question_key(text: str) -> str:
    return hashlib.md5(_normalize_question(text).encode()).hexdigest()


class SqliteQARepository(SqliteBaseRepo, QARepository):
    def store(self, question_text: str, question_key: str, answer_text: str, answer_source: str, **kwargs) -> None:
        now = datetime.now(timezone.utc).isoformat()

        def _do():
            self._conn.execute(
                "INSERT OR REPLACE INTO qa_knowledge "
                "(question_text, question_key, answer_text, answer_source, "
                "field_type, options_json, ats_slug, job_url, outcome, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    question_text,
                    question_key,
                    answer_text,
                    answer_source,
                    kwargs.get("field_type"),
                    kwargs.get("options_json"),
                    kwargs.get("ats_slug"),
                    kwargs.get("job_url"),
                    kwargs.get("outcome", "unknown"),
                    now,
                    now,
                ),
            )

        self._write(_do)

    def lookup(self, question_key: str) -> "QAKnowledgeDTO | None":
        from applypilot.db.dto import QAKnowledgeDTO

        row = self._conn.execute(
            "SELECT * FROM qa_knowledge WHERE question_key=? ORDER BY updated_at DESC LIMIT 1", (question_key,)
        ).fetchone()
        return self._row_to_dto(row, QAKnowledgeDTO) if row else None

    def get_all(self) -> "list[QAKnowledgeDTO]":
        from applypilot.db.dto import QAKnowledgeDTO

        rows = self._conn.execute("SELECT * FROM qa_knowledge ORDER BY updated_at DESC").fetchall()
        return [self._row_to_dto(r, QAKnowledgeDTO) for r in rows]
