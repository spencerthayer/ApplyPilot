from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from applypilot.scoring import cover_letter


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from applypilot.db.schema import init_db

    init_db(conn)
    return conn


def _insert_job(conn: sqlite3.Connection, url: str, title: str, site: str, score: int) -> None:
    conn.execute(
        "INSERT INTO jobs (url, title, site, fit_score, tailored_resume_path, "
        "full_description, cover_letter_path, cover_letter_at, cover_attempts) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 0)",
        (url, title, site, score, "tailored.txt", "Full job description"),
    )
    conn.commit()


def _mock_app(conn, monkeypatch):
    from applypilot.db.sqlite.job_repo import SqliteJobRepository

    repo = SqliteJobRepository(conn)
    app = SimpleNamespace(container=SimpleNamespace(job_repo=repo), config=SimpleNamespace())
    monkeypatch.setattr("applypilot.bootstrap.get_app", lambda: app)


def _fake_convert_to_pdf(text_path: Path) -> Path:
    out = text_path.with_suffix(".pdf")
    out.write_bytes(b"%PDF-1.4 fake\n")
    return out


def test_run_cover_letters_limit_zero_processes_all_and_avoids_filename_collisions(monkeypatch, tmp_path: Path) -> None:
    conn = _make_conn()
    _insert_job(conn, "https://www.linkedin.com/jobs/view/111", "Network Engineer V", "LinkedIn", 9)
    _insert_job(conn, "https://www.linkedin.com/jobs/view/222", "Network Engineer V", "LinkedIn", 8)
    _insert_job(conn, "https://www.linkedin.com/jobs/view/333", "Systems Engineer", "LinkedIn", 7)

    monkeypatch.setattr(cover_letter, "COVER_LETTER_DIR", tmp_path)
    _mock_app(conn, monkeypatch)
    monkeypatch.setattr(cover_letter, "load_profile", lambda: {"personal": {"full_name": "Alex Example"}})
    monkeypatch.setattr(cover_letter, "load_resume_text", lambda: "base resume")
    monkeypatch.setattr(cover_letter, "generate_cover_letter", lambda *args, **kwargs: "Dear Hiring Manager,\nAlex")
    monkeypatch.setattr("applypilot.scoring.pdf.convert_to_pdf", _fake_convert_to_pdf)

    result = cover_letter.run_cover_letters(min_score=7, limit=0, validation_mode="normal")

    assert result["generated"] == 3
    saved_count = conn.execute("SELECT COUNT(*) FROM jobs WHERE cover_letter_path IS NOT NULL").fetchone()[0]
    assert saved_count == 3

    names = sorted(path.name for path in tmp_path.glob("*_CL.txt"))
    assert len(names) == 3
    assert any("_111_CL.txt" in name for name in names)
    assert any("_222_CL.txt" in name for name in names)


def test_run_cover_letters_positive_limit_caps_processing(monkeypatch, tmp_path: Path) -> None:
    conn = _make_conn()
    _insert_job(conn, "https://www.linkedin.com/jobs/view/111", "Network Engineer V", "LinkedIn", 10)
    _insert_job(conn, "https://www.linkedin.com/jobs/view/222", "Network Engineer V", "LinkedIn", 9)
    _insert_job(conn, "https://www.linkedin.com/jobs/view/333", "Network Engineer V", "LinkedIn", 8)

    monkeypatch.setattr(cover_letter, "COVER_LETTER_DIR", tmp_path)
    _mock_app(conn, monkeypatch)
    monkeypatch.setattr(cover_letter, "load_profile", lambda: {"personal": {"full_name": "Alex Example"}})
    monkeypatch.setattr(cover_letter, "load_resume_text", lambda: "base resume")
    monkeypatch.setattr(cover_letter, "generate_cover_letter", lambda *args, **kwargs: "Dear Hiring Manager,\nAlex")
    monkeypatch.setattr("applypilot.scoring.pdf.convert_to_pdf", _fake_convert_to_pdf)

    result = cover_letter.run_cover_letters(min_score=7, limit=2, validation_mode="normal")

    assert result["generated"] == 2
    saved_count = conn.execute("SELECT COUNT(*) FROM jobs WHERE cover_letter_path IS NOT NULL").fetchone()[0]
    assert saved_count == 2
    third_path = conn.execute(
        "SELECT cover_letter_path FROM jobs WHERE url = ?",
        ("https://www.linkedin.com/jobs/view/333",),
    ).fetchone()[0]
    assert third_path is None
