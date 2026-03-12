from __future__ import annotations

from applypilot import pipeline


def _normalize_sql(sql: str) -> str:
    return " ".join(sql.split())


def test_stream_pending_score_sql_matches_retry_window_semantics() -> None:
    expected = (
        "SELECT COUNT(*) FROM jobs WHERE full_description IS NOT NULL AND ("
        "  (fit_score IS NULL AND score_error IS NULL) "
        "  OR (score_error IS NOT NULL AND score_retry_count < 5 "
        "      AND (score_next_retry_at IS NULL OR score_next_retry_at <= datetime('now')))"
        ")"
    )
    assert _normalize_sql(pipeline._PENDING_SQL["score"]) == _normalize_sql(expected)
