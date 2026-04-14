from __future__ import annotations


def _insert_job(
    conn,
    *,
    url: str,
    site: str,
    application_url: str,
    score: int,
) -> None:
    conn.execute(
        """
        INSERT INTO jobs (
            url, title, site, application_url, tailored_resume_path, fit_score, apply_status, apply_attempts
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, 0)
        """,
        (url, "Test Job", site, application_url, "/tmp/resume.pdf", score),
    )
    conn.commit()


def test_acquire_job_skips_manual_ats_and_returns_next_actionable(monkeypatch, tmp_path):
    import sqlite3
    from types import SimpleNamespace
    from applypilot.db.schema import init_db
    from applypilot.db.sqlite.job_repo import SqliteJobRepository
    from applypilot.apply import launcher

    conn = sqlite3.connect(str(tmp_path / "applypilot.db"))
    conn.row_factory = sqlite3.Row
    init_db(conn)

    manual_url = (
        "https://thomsonreuters.wd5.myworkdayjobs.com/External_Career_Site/job/"
        "Canada-Toronto-Ontario/Lead-Software-Engineer---AI-_JREQ196460"
    )
    actionable_url = "https://www.linkedin.com/jobs/view/4383377387"

    _insert_job(conn, url=manual_url, site="Thomson Reuters", application_url=manual_url, score=9)
    _insert_job(conn, url=actionable_url, site="linkedin", application_url=actionable_url, score=8)

    repo = SqliteJobRepository(conn)
    mock_app = SimpleNamespace(container=SimpleNamespace(job_repo=repo), config=SimpleNamespace())
    monkeypatch.setattr("applypilot.bootstrap.get_app", lambda: mock_app)
    monkeypatch.setattr(launcher, "_load_blocked", lambda: (set(), []))

    job = launcher.acquire_job(min_score=7, worker_id=2)

    assert job is not None
    assert job["url"] == actionable_url

    # CHANGED: manual ATS jobs are now parked as 'needs_human' for HITL review
    # instead of silently marked 'manual'. See launcher.py acquire_job().
    manual = conn.execute(
        "SELECT apply_status, needs_human_reason FROM jobs WHERE url = ?",
        (manual_url,),
    ).fetchone()
    assert manual["apply_status"] == "needs_human"
    assert "manual ATS" in manual["needs_human_reason"]

    acquired = conn.execute(
        "SELECT apply_status, agent_id FROM jobs WHERE url = ?",
        (actionable_url,),
    ).fetchone()
    assert acquired["apply_status"] == "in_progress"
    assert acquired["agent_id"] == "worker-2"


def test_acquire_job_target_url_manual_allows_attempt(monkeypatch, tmp_path):
    import sqlite3
    from types import SimpleNamespace
    from applypilot.db.schema import init_db as _init_db
    from applypilot.db.sqlite.job_repo import SqliteJobRepository
    from applypilot.apply import launcher

    conn = sqlite3.connect(str(tmp_path / "applypilot.db"))
    conn.row_factory = sqlite3.Row
    _init_db(conn)
    manual_url = (
        "https://thomsonreuters.wd5.myworkdayjobs.com/External_Career_Site/job/"
        "Canada-Toronto-Ontario/Senior-Software-Engineer--AI_JREQ194874"
    )

    _insert_job(conn, url=manual_url, site="Thomson Reuters", application_url=manual_url, score=9)

    repo = SqliteJobRepository(conn)
    mock_app = SimpleNamespace(container=SimpleNamespace(job_repo=repo), config=SimpleNamespace())
    monkeypatch.setattr("applypilot.bootstrap.get_app", lambda: mock_app)

    job = launcher.acquire_job(target_url=manual_url, min_score=7, worker_id=0)

    assert job is not None
    assert job["url"] == manual_url
    manual = conn.execute(
        "SELECT apply_status, apply_error, agent_id FROM jobs WHERE url = ?",
        (manual_url,),
    ).fetchone()
    assert manual["apply_status"] == "in_progress"
    assert manual["apply_error"] is None
    assert manual["agent_id"] == "worker-0"


def test_target_unavailable_reason_reports_missing_resume_before_other_checks(monkeypatch, tmp_path):
    import sqlite3
    from types import SimpleNamespace
    from applypilot.db.schema import init_db as _init_db
    from applypilot.db.sqlite.job_repo import SqliteJobRepository
    from applypilot.apply import launcher

    conn = sqlite3.connect(str(tmp_path / "applypilot.db"))
    conn.row_factory = sqlite3.Row
    _init_db(conn)
    url = "https://example.com/job/123"
    conn.execute(
        "INSERT INTO jobs (url, title, site, application_url, tailored_resume_path, fit_score, apply_status, apply_attempts) "
        "VALUES (?, ?, ?, ?, NULL, ?, NULL, 0)",
        (url, "Test Job", "Example", url, 9),
    )
    conn.commit()

    repo = SqliteJobRepository(conn)
    mock_app = SimpleNamespace(container=SimpleNamespace(job_repo=repo), config=SimpleNamespace())
    monkeypatch.setattr("applypilot.bootstrap.get_app", lambda: mock_app)

    reason = launcher._target_unavailable_reason(url, min_score=7)
    assert reason == "missing tailored resume for this job"
