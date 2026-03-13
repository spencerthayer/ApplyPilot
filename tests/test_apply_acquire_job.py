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
    from applypilot import database
    from applypilot.apply import launcher

    conn = database.init_db(tmp_path / "applypilot.db")
    manual_url = (
        "https://thomsonreuters.wd5.myworkdayjobs.com/External_Career_Site/job/"
        "Canada-Toronto-Ontario/Lead-Software-Engineer---AI-_JREQ196460"
    )
    actionable_url = "https://www.linkedin.com/jobs/view/4383377387"

    _insert_job(
        conn,
        url=manual_url,
        site="Thomson Reuters",
        application_url=manual_url,
        score=9,
    )
    _insert_job(
        conn,
        url=actionable_url,
        site="linkedin",
        application_url=actionable_url,
        score=8,
    )

    monkeypatch.setattr(launcher, "get_connection", lambda: conn)
    monkeypatch.setattr(launcher, "_load_blocked", lambda: (set(), []))

    job = launcher.acquire_job(min_score=7, worker_id=2)

    assert job is not None
    assert job["url"] == actionable_url

    manual = conn.execute(
        "SELECT apply_status, apply_error FROM jobs WHERE url = ?",
        (manual_url,),
    ).fetchone()
    assert manual["apply_status"] == "manual"
    assert manual["apply_error"] == "manual ATS"

    acquired = conn.execute(
        "SELECT apply_status, agent_id FROM jobs WHERE url = ?",
        (actionable_url,),
    ).fetchone()
    assert acquired["apply_status"] == "in_progress"
    assert acquired["agent_id"] == "worker-2"


def test_acquire_job_target_url_manual_marks_manual_and_returns_none(monkeypatch, tmp_path):
    from applypilot import database
    from applypilot.apply import launcher

    conn = database.init_db(tmp_path / "applypilot.db")
    manual_url = (
        "https://thomsonreuters.wd5.myworkdayjobs.com/External_Career_Site/job/"
        "Canada-Toronto-Ontario/Senior-Software-Engineer--AI_JREQ194874"
    )

    _insert_job(
        conn,
        url=manual_url,
        site="Thomson Reuters",
        application_url=manual_url,
        score=9,
    )

    monkeypatch.setattr(launcher, "get_connection", lambda: conn)

    job = launcher.acquire_job(target_url=manual_url, min_score=7, worker_id=0)

    assert job is None
    manual = conn.execute(
        "SELECT apply_status, apply_error FROM jobs WHERE url = ?",
        (manual_url,),
    ).fetchone()
    assert manual["apply_status"] == "manual"
    assert manual["apply_error"] == "manual ATS"
