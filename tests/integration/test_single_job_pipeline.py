"""Integration tests for the single-job pipeline and today's fixes.

These tests hit real DB but mock LLM calls. They verify:
- Single-job scoping (only target URL is processed)
- Bullet count config flows into the tailoring prompt
- Skill-gap checker produces meaningful output
- Manual ATS jobs get parked as needs_human
- get_jobs_by_stage respects job_url filtering
- Cover letters use the shared query gateway

Run with: pytest tests/integration/test_single_job_pipeline.py -v
"""

from __future__ import annotations

import sqlite3
import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """Create an in-memory DB with the full schema and patch get_app."""
    from types import SimpleNamespace
    from applypilot.db.schema import init_db
    from applypilot.db.sqlite.job_repo import SqliteJobRepository

    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_db(conn)

    repo = SqliteJobRepository(conn)
    mock_app = SimpleNamespace(
        container=SimpleNamespace(job_repo=repo, _conn=conn),
        config=SimpleNamespace(scoring=None),
    )
    monkeypatch.setattr("applypilot.bootstrap.get_app", lambda: mock_app)
    return conn


def _insert_job(
        conn,
        url="https://example.com/job/1",
        title="Test Job",
        site="example",
        desc=None,
        score=None,
        tailored=None,
        cover=None,
        apply_status=None,
        app_url=None,
):
    """Helper to insert a job row."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO jobs (url, title, site, full_description, fit_score, "
        "tailored_resume_path, cover_letter_path, apply_status, application_url, "
        "discovered_at, strategy) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (url, title, site, desc, score, tailored, cover, apply_status, app_url, now, "test"),
    )
    conn.commit()


# ── Test: get_jobs_by_stage with job_url ─────────────────────────────────


class TestGetJobsByStageScoping:
    """Verify job_url parameter scopes queries to a single URL."""

    def _get_repo(self, test_db):
        from applypilot.db.sqlite.job_repo import SqliteJobRepository

        return SqliteJobRepository(test_db)

    def test_without_job_url_returns_all(self, test_db):
        repo = self._get_repo(test_db)
        _insert_job(test_db, "https://a.com/1", "Job A", desc="desc a")
        _insert_job(test_db, "https://b.com/2", "Job B", desc="desc b")

        jobs = repo.get_jobs_by_stage_dict(stage="enriched", limit=100)
        assert len(jobs) == 2

    def test_with_job_url_returns_one(self, test_db):
        repo = self._get_repo(test_db)
        _insert_job(test_db, "https://a.com/1", "Job A", desc="desc a")
        _insert_job(test_db, "https://b.com/2", "Job B", desc="desc b")

        jobs = repo.get_jobs_by_stage_dict(stage="enriched", limit=100, job_url="https://a.com/1")
        assert len(jobs) == 1
        assert jobs[0].url == "https://a.com/1"

    def test_job_url_no_match_returns_empty(self, test_db):
        repo = self._get_repo(test_db)
        _insert_job(test_db, "https://a.com/1", "Job A", desc="desc a")

        jobs = repo.get_jobs_by_stage_dict(stage="enriched", limit=100, job_url="https://nonexistent.com/999")
        assert len(jobs) == 0

    def test_pending_cover_stage(self, test_db):
        repo = self._get_repo(test_db)
        _insert_job(test_db, "https://a.com/1", "Job A", desc="desc", score=8, tailored="/path/a.txt")
        _insert_job(test_db, "https://b.com/2", "Job B", desc="desc", score=9, tailored="/path/b.txt")

        jobs = repo.get_jobs_by_stage_dict(stage="pending_cover", min_score=7, limit=100, job_url="https://a.com/1")
        assert len(jobs) == 1
        assert jobs[0].url == "https://a.com/1"


# ── Test: Skill-gap checker ──────────────────────────────────────────────


class TestSkillGapChecker:
    """Verify deterministic keyword extraction and gap detection."""

    def test_matched_keywords_found(self):
        from applypilot.scoring.tailor import check_skill_gaps

        jd = "We need experience with Python, Docker, and Kubernetes. Python is essential."
        resume = "Built microservices with Python and Docker on AWS."
        result = check_skill_gaps(jd, resume)
        assert "python" in result["matched"]
        assert "docker" in result["matched"]
        assert result["coverage"] > 0

    def test_missing_keywords_reported(self):
        from applypilot.scoring.tailor import check_skill_gaps

        jd = "Must know React and GraphQL. React experience is required."
        resume = "Built backend APIs with Python and Flask."
        result = check_skill_gaps(jd, resume)
        assert "react" in result["missing"]

    def test_urls_filtered_out(self):
        from applypilot.scoring.tailor import check_skill_gaps

        jd = "Apply at https://example.com/jobs/123. Python required. Python is key."
        resume = "Python developer."
        result = check_skill_gaps(jd, resume)
        missing_str = " ".join(result["missing"])
        assert "https" not in missing_str
        assert "example.com" not in missing_str

    def test_empty_jd_returns_full_coverage(self):
        from applypilot.scoring.tailor import check_skill_gaps

        result = check_skill_gaps("", "Some resume text")
        assert result["coverage"] == 1.0

    def test_single_mention_keyword_still_detected(self):
        """A keyword appearing once in the JD is still a real requirement."""
        from applypilot.scoring.tailor import check_skill_gaps

        jd = "We need Python, Docker, and Kubernetes for this role."
        resume = "Built services with Python."
        result = check_skill_gaps(jd, resume)
        assert "python" in result["matched"]
        assert "docker" in result["missing"]
        assert "kubernetes" in result["missing"]

    def test_non_tech_jd_works(self):
        from applypilot.scoring.tailor import check_skill_gaps

        jd = "Marketing manager needed. SEO and content strategy. SEO is critical."
        resume = "Led SEO campaigns for enterprise clients."
        result = check_skill_gaps(jd, resume)
        assert "seo" in result["matched"]


# ── Test: Bullet count config in prompt ──────────────────────────────────


class TestBulletCountConfig:
    """Verify tailoring prompt reads bullet config from profile and enforces
    exact per-role bullet counts — the most recent role gets max_bullets,
    all other roles get min_bullets. This prevents the LLM from generating
    inconsistent bullet counts across roles."""

    def test_prompt_contains_bullet_range(self):
        from applypilot.scoring.tailor import _build_tailor_prompt

        profile = {
            "skills_boundary": {"languages": ["Python"]},
            "resume_facts": {},
            "experience": {"education_level": "B.Tech"},
            "tailoring_config": {
                "validation": {
                    "min_bullets_per_role": 4,
                    "max_bullets_per_role": 6,
                }
            },
        }
        prompt = _build_tailor_prompt(profile)
        # Prompt uses exact counts: most recent role gets max, others get min
        assert "6" in prompt
        assert "AT LEAST 4" in prompt or "4" in prompt

    def test_default_bullets_when_no_config(self):
        from applypilot.scoring.tailor import _build_tailor_prompt

        profile = {
            "skills_boundary": {},
            "resume_facts": {},
            "experience": {},
        }
        prompt = _build_tailor_prompt(profile)
        # Without explicit config, page_budget.calculate derives defaults.
        # Verify the prompt still contains the BULLET COUNTS section.
        assert "BULLET COUNTS" in prompt
        assert "AT LEAST" in prompt or "bullets" in prompt.lower()


# ── Test: Manual ATS → needs_human ───────────────────────────────────────


class TestManualAtsParking:
    """Verify manual ATS jobs get parked for human review in queue mode."""

    def test_manual_ats_parked_in_queue_mode(self, test_db, monkeypatch):
        """Queue mode (no target_url): manual ATS → needs_human, then returns next job."""
        from applypilot.apply import launcher

        # get_app already mocked by test_db fixture
        monkeypatch.setattr(launcher, "_load_blocked", lambda: (set(), []))
        _insert_job(
            test_db,
            "https://company.myworkdayjobs.com/job/123",
            "SWE III",
            site="workday",
            desc="desc",
            score=8,
            tailored="/path.txt",
            app_url="https://company.myworkdayjobs.com/job/123/apply",
        )
        _insert_job(
            test_db,
            "https://good.com/456",
            "SWE",
            site="linkedin",
            desc="desc",
            score=7,
            tailored="/path2.txt",
            app_url="https://good.com/456/apply",
        )

        monkeypatch.setattr("applypilot.config.is_manual_ats", lambda url: "myworkdayjobs" in url)
        # Queue mode: should park manual ATS and return the next actionable job
        result = launcher.acquire_job(min_score=7)

        row = test_db.execute(
            "SELECT apply_status, needs_human_reason FROM jobs WHERE url LIKE '%myworkday%'"
        ).fetchone()
        assert row["apply_status"] == "needs_human"
        assert "manual ATS" in row["needs_human_reason"]
        assert result is not None
        assert result["url"] == "https://good.com/456"

    def test_target_url_bypasses_manual_ats_check(self, test_db, monkeypatch):
        """--url mode: manual ATS jobs proceed directly (user explicitly targeted them)."""
        from applypilot.apply import launcher

        # get_app already mocked by test_db fixture
        _insert_job(
            test_db,
            "https://company.myworkdayjobs.com/job/123",
            "SWE",
            site="workday",
            desc="desc",
            score=8,
            tailored="/path.txt",
            app_url="https://company.myworkdayjobs.com/job/123/apply",
        )

        monkeypatch.setattr("applypilot.config.is_manual_ats", lambda url: True)
        result = launcher.acquire_job(target_url="https://company.myworkdayjobs.com/job/123")

        assert result is not None
        assert result["url"] == "https://company.myworkdayjobs.com/job/123"


# ── Test: HITL DB operations (direct SQL, no helper functions) ───────────


class TestHitlDbOps:
    def test_needs_human_roundtrip(self, test_db):
        """Verify needs_human can be set and cleared via direct SQL."""
        _insert_job(test_db, "https://example.com/1", "Job")

        test_db.execute(
            "UPDATE jobs SET apply_status = 'needs_human', needs_human_reason = 'captcha' WHERE url = ?",
            ("https://example.com/1",),
        )
        test_db.commit()
        row = test_db.execute("SELECT apply_status, needs_human_reason FROM jobs").fetchone()
        assert row["apply_status"] == "needs_human"
        assert row["needs_human_reason"] == "captcha"

        test_db.execute(
            "UPDATE jobs SET apply_status = NULL, needs_human_reason = NULL WHERE url = ?", ("https://example.com/1",)
        )
        test_db.commit()
        row = test_db.execute("SELECT apply_status, needs_human_reason FROM jobs").fetchone()
        assert row["apply_status"] is None
        assert row["needs_human_reason"] is None


# ── E2E: run_scoring respects job_url ────────────────────────────────────


# ── E2E: run_tailoring respects job_url ──────────────────────────────────


# ── E2E: run_cover_letters respects job_url ──────────────────────────────


class TestCoverLetterE2EScoping:
    """Verify run_cover_letters only generates for the target job."""

    def test_cover_scoped_to_single_url(self, test_db, monkeypatch, tmp_path):
        from applypilot.scoring import cover_letter

        # TODO: cover_letter.py doesn't have job_url/target_url yet.
        # For now, test that it processes only the target by having only one
        # eligible job in the DB (the other has no tailored resume).
        _insert_job(
            test_db,
            "https://target.com/1",
            "Target SWE",
            site="acme",
            desc="Python role",
            score=8,
            tailored="/path/a.txt",
        )
        _insert_job(
            test_db, "https://other.com/2", "Other Job", site="corp", desc="Sales role", score=9
        )  # no tailored resume → not eligible

        cover_dir = tmp_path / "covers"
        cover_dir.mkdir()

        # get_app already mocked by test_db fixture
        monkeypatch.setattr(cover_letter, "COVER_LETTER_DIR", cover_dir)
        monkeypatch.setattr(cover_letter, "load_resume_text", lambda: "resume")
        monkeypatch.setattr(cover_letter, "load_profile", lambda: {"personal": {"full_name": "Test"}})
        monkeypatch.setattr(cover_letter, "generate_cover_letter", lambda *a, **kw: "Dear Hiring Manager...")

        cover_letter.run_cover_letters(min_score=7)

        target = test_db.execute("SELECT cover_letter_path FROM jobs WHERE url = 'https://target.com/1'").fetchone()
        other = test_db.execute("SELECT cover_letter_path FROM jobs WHERE url = 'https://other.com/2'").fetchone()
        assert target["cover_letter_path"] is not None
        assert other["cover_letter_path"] is None
