from __future__ import annotations

import sqlite3

import applypilot.scoring.scorer as scorer


def _make_jobs_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE jobs (
            url TEXT PRIMARY KEY,
            title TEXT,
            site TEXT,
            location TEXT,
            full_description TEXT,
            fit_score INTEGER,
            score_reasoning TEXT,
            scored_at TEXT,
            exclusion_reason_code TEXT,
            exclusion_rule_id TEXT,
            excluded_at TEXT,
            score_error TEXT,
            score_retry_count INTEGER DEFAULT 0,
            score_next_retry_at TEXT
        )
        """
    )
    conn.commit()
    return conn


def _insert_job(conn: sqlite3.Connection, url: str = "https://example.com/job/1") -> None:
    conn.execute(
        """
        INSERT INTO jobs (
            url, title, site, location, full_description, fit_score, score_reasoning,
            scored_at, exclusion_reason_code, exclusion_rule_id, excluded_at,
            score_error, score_retry_count, score_next_retry_at
        ) VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0, NULL)
        """,
        (url, "Backend Engineer", "ExampleCo", "Remote", "Python and APIs"),
    )
    conn.commit()


def test_llm_failure_writes_retry_metadata_and_keeps_fit_score_null(monkeypatch) -> None:
    conn = _make_jobs_conn()
    _insert_job(conn)

    monkeypatch.setattr(scorer, "get_connection", lambda: conn)
    monkeypatch.setattr(scorer, "_load_scoring_resume_text", lambda: "resume")
    monkeypatch.setattr(
        scorer,
        "get_jobs_by_stage",
        lambda **_: [
            {
                "url": "https://example.com/job/1",
                "title": "Backend Engineer",
                "site": "ExampleCo",
                "location": "Remote",
                "full_description": "Python and APIs",
                "score_retry_count": 0,
            }
        ],
    )
    monkeypatch.setattr(scorer, "evaluate_exclusion", lambda _: None)
    monkeypatch.setattr(
        scorer,
        "score_job",
        lambda *_: {"score": 0, "keywords": "", "reasoning": "LLM error: request failed"},
    )

    result = scorer.run_scoring()

    row = conn.execute(
        "SELECT fit_score, score_error, score_retry_count, score_next_retry_at FROM jobs WHERE url = ?",
        ("https://example.com/job/1",),
    ).fetchone()
    assert result["errors"] == 1
    assert row["fit_score"] is None
    assert row["score_error"].startswith("LLM error:")
    assert row["score_retry_count"] == 1
    assert row["score_next_retry_at"] is not None


def test_success_clears_retry_and_error_fields(monkeypatch) -> None:
    conn = _make_jobs_conn()
    _insert_job(conn)
    conn.execute(
        "UPDATE jobs SET score_error = ?, score_retry_count = 3, score_next_retry_at = ? WHERE url = ?",
        ("LLM error: old failure", "2099-01-01T00:00:00+00:00", "https://example.com/job/1"),
    )
    conn.commit()

    monkeypatch.setattr(scorer, "get_connection", lambda: conn)
    monkeypatch.setattr(scorer, "_load_scoring_resume_text", lambda: "resume")
    monkeypatch.setattr(
        scorer,
        "get_jobs_by_stage",
        lambda **_: [
            {
                "url": "https://example.com/job/1",
                "title": "Backend Engineer",
                "site": "ExampleCo",
                "location": "Remote",
                "full_description": "Python and APIs",
                "score_retry_count": 3,
            }
        ],
    )
    monkeypatch.setattr(scorer, "evaluate_exclusion", lambda _: None)
    monkeypatch.setattr(
        scorer,
        "score_job",
        lambda *_: {"score": 8, "keywords": "python,apis", "reasoning": "Strong fit."},
    )

    scorer.run_scoring()
    row = conn.execute(
        "SELECT fit_score, score_error, score_retry_count, score_next_retry_at, exclusion_reason_code FROM jobs WHERE url = ?",
        ("https://example.com/job/1",),
    ).fetchone()

    assert row["fit_score"] == 8
    assert row["score_error"] is None
    assert row["score_retry_count"] == 0
    assert row["score_next_retry_at"] is None
    assert row["exclusion_reason_code"] is None


def test_exclusions_remain_score_zero_and_clear_retry_fields(monkeypatch) -> None:
    conn = _make_jobs_conn()
    _insert_job(conn)
    conn.execute(
        "UPDATE jobs SET score_error = ?, score_retry_count = 2, score_next_retry_at = ? WHERE url = ?",
        ("LLM error: stale", "2099-01-01T00:00:00+00:00", "https://example.com/job/1"),
    )
    conn.commit()

    monkeypatch.setattr(scorer, "get_connection", lambda: conn)
    monkeypatch.setattr(scorer, "_load_scoring_resume_text", lambda: "resume")
    monkeypatch.setattr(
        scorer,
        "get_jobs_by_stage",
        lambda **_: [
            {
                "url": "https://example.com/job/1",
                "title": "Backend Engineer Intern",
                "site": "ExampleCo",
                "location": "Remote",
                "full_description": "Intern role",
                "score_retry_count": 2,
            }
        ],
    )
    monkeypatch.setattr(
        scorer,
        "evaluate_exclusion",
        lambda _: {
            "score": 0,
            "keywords": "",
            "reasoning": "EXCLUDED: excluded_keyword - matched 'intern' (rule r-001)",
            "exclusion_reason_code": "excluded_keyword",
            "exclusion_rule_id": "r-001",
        },
    )
    monkeypatch.setattr(scorer, "score_job", lambda *_: (_ for _ in ()).throw(AssertionError("score_job must not run")))

    scorer.run_scoring()
    row = conn.execute(
        """
        SELECT fit_score, exclusion_reason_code, exclusion_rule_id,
               score_error, score_retry_count, score_next_retry_at
        FROM jobs WHERE url = ?
        """,
        ("https://example.com/job/1",),
    ).fetchone()

    assert row["fit_score"] == 0
    assert row["exclusion_reason_code"] == "excluded_keyword"
    assert row["exclusion_rule_id"] == "r-001"
    assert row["score_error"] is None
    assert row["score_retry_count"] == 0
    assert row["score_next_retry_at"] is None


def test_autoheal_repairs_only_legacy_llm_error_rows() -> None:
    conn = _make_jobs_conn()
    conn.execute(
        """
        INSERT INTO jobs (
            url, title, site, location, full_description, fit_score, score_reasoning,
            exclusion_reason_code, exclusion_rule_id, score_retry_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "https://example.com/poisoned",
            "Engineer",
            "ExampleCo",
            "Remote",
            "Desc",
            0,
            "\nLLM error: legacy failure",
            None,
            None,
            0,
        ),
    )
    conn.execute(
        """
        INSERT INTO jobs (
            url, title, site, location, full_description, fit_score, score_reasoning,
            exclusion_reason_code, exclusion_rule_id, score_retry_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "https://example.com/excluded",
            "Intern Engineer",
            "ExampleCo",
            "Remote",
            "Desc",
            0,
            "EXCLUDED: excluded_keyword",
            "excluded_keyword",
            "r-001",
            0,
        ),
    )
    conn.commit()

    healed = scorer._autoheal_legacy_llm_failures(conn)

    poisoned = conn.execute(
        "SELECT fit_score, score_error, score_retry_count, score_next_retry_at FROM jobs WHERE url = ?",
        ("https://example.com/poisoned",),
    ).fetchone()
    excluded = conn.execute(
        "SELECT fit_score, exclusion_reason_code, exclusion_rule_id FROM jobs WHERE url = ?",
        ("https://example.com/excluded",),
    ).fetchone()

    assert healed == 1
    assert poisoned["fit_score"] is None
    assert poisoned["score_error"].startswith("LLM error:")
    assert poisoned["score_retry_count"] == 1
    assert poisoned["score_next_retry_at"] is None
    assert excluded["fit_score"] == 0
    assert excluded["exclusion_reason_code"] == "excluded_keyword"
    assert excluded["exclusion_rule_id"] == "r-001"
