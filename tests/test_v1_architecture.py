"""Tests for V1 architecture: Profile, Bootstrap, RuntimeConfig, Services, Repo methods."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

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
from applypilot.db.schema import init_db
from applypilot.db.sqlite.job_repo import SqliteJobRepository


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def repo(conn):
    return SqliteJobRepository(conn)


@pytest.fixture
def now():
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def sample_job(now):
    return JobDTO(url="https://example.com/job-1", title="SDE", site="test", discovered_at=now)


# ── Profile ──────────────────────────────────────────────────────────


class TestProfile:
    def test_default_paths(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
        # Re-import to pick up new env
        from applypilot.profile import Profile

        p = Profile(root_dir=tmp_path)
        assert p.db_path == tmp_path / "applypilot.db"
        assert p.resume_json_path == tmp_path / "resume.json"
        assert p.env_path == tmp_path / ".env"

    def test_not_initialized(self, tmp_path):
        from applypilot.profile import Profile

        p = Profile(root_dir=tmp_path)
        assert not p.is_initialized
        assert not p.has_profile

    def test_initialized_with_resume(self, tmp_path):
        from applypilot.profile import Profile

        (tmp_path / "resume.json").write_text('{"basics":{"name":"Test"}}')
        p = Profile(root_dir=tmp_path)
        assert p.is_initialized

    def test_ensure_dirs(self, tmp_path):
        from applypilot.profile import Profile

        p = Profile(root_dir=tmp_path / "new")
        p.ensure_dirs()
        assert p.root_dir.exists()
        assert p.tailored_dir.exists()
        assert p.log_dir.exists()

    def test_load_resume_json(self, tmp_path):
        from applypilot.profile import Profile

        data = {"basics": {"name": "Alice", "label": "Engineer"}}
        (tmp_path / "resume.json").write_text(json.dumps(data))
        p = Profile(root_dir=tmp_path)
        loaded = p.load_resume_json()
        assert loaded["basics"]["name"] == "Alice"

    def test_load_settings(self, tmp_path):
        from applypilot.profile import Profile

        (tmp_path / "profile.json").write_text('{"work_authorization": true}')
        p = Profile(root_dir=tmp_path)
        settings = p.load_settings()
        assert settings["work_authorization"] is True

    def test_summary(self, tmp_path):
        from applypilot.profile import Profile

        (tmp_path / "resume.json").write_text('{"basics":{"name":"Bob"}}')
        p = Profile(root_dir=tmp_path)
        s = p.summary()
        assert s["name"] == "Bob"
        assert s["initialized"] is True


# ── RuntimeConfig ────────────────────────────────────────────────────


class TestRuntimeConfig:
    def test_defaults(self):
        from applypilot.runtime_config import RuntimeConfig

        rc = RuntimeConfig()
        assert rc.scoring.min_score == 7
        assert rc.tailoring.retention_threshold == 0.40
        assert rc.apply.max_attempts == 10
        assert rc.pipeline.chunk_size == 1000

    def test_load_missing_file(self, tmp_path):
        from applypilot.runtime_config import RuntimeConfig

        rc = RuntimeConfig.load(tmp_path / "nonexistent.yaml")
        assert rc.scoring.min_score == 7  # defaults

    def test_load_yaml_overrides(self, tmp_path):
        import yaml
        from applypilot.runtime_config import RuntimeConfig

        cfg = {"scoring": {"min_score": 9}, "apply": {"rate_limit_per_hour": 20}}
        (tmp_path / "config.yaml").write_text(yaml.dump(cfg))
        rc = RuntimeConfig.load(tmp_path / "config.yaml")
        assert rc.scoring.min_score == 9
        assert rc.apply.rate_limit_per_hour == 20
        # Unset values keep defaults
        assert rc.scoring.max_attempts_per_job == 3
        assert rc.tailoring.retention_threshold == 0.40

    def test_load_invalid_yaml(self, tmp_path):
        from applypilot.runtime_config import RuntimeConfig

        (tmp_path / "config.yaml").write_text("{{invalid yaml")
        rc = RuntimeConfig.load(tmp_path / "config.yaml")
        assert rc.scoring.min_score == 7  # falls back to defaults

    def test_frozen(self):
        from applypilot.runtime_config import RuntimeConfig

        rc = RuntimeConfig()
        with pytest.raises(AttributeError):
            rc.scoring = None  # type: ignore

    def test_ignores_unknown_keys(self, tmp_path):
        import yaml
        from applypilot.runtime_config import RuntimeConfig

        cfg = {"scoring": {"min_score": 5, "unknown_key": "ignored"}}
        (tmp_path / "config.yaml").write_text(yaml.dump(cfg))
        rc = RuntimeConfig.load(tmp_path / "config.yaml")
        assert rc.scoring.min_score == 5
        assert not hasattr(rc.scoring, "unknown_key")


# ── JobRepository — New Methods ──────────────────────────────────────


class TestJobRepoEnrichment:
    def test_update_enrichment(self, repo, sample_job, now):
        repo.upsert(sample_job)
        repo.update_enrichment(
            EnrichResultDTO(
                url=sample_job.url,
                full_description="Full JD",
                application_url="https://apply.example.com",
                detail_scraped_at=now,
            )
        )
        job = repo.get_by_url(sample_job.url)
        assert job.full_description == "Full JD"
        assert job.application_url == "https://apply.example.com"

    def test_update_enrichment_error(self, repo, sample_job, now):
        repo.upsert(sample_job)
        repo.update_enrichment_error(
            EnrichErrorDTO(
                url=sample_job.url,
                detail_error="timeout",
                detail_error_category="retriable",
                detail_retry_count=1,
                detail_next_retry_at=now,
                detail_scraped_at=now,
            )
        )
        job = repo.get_by_url(sample_job.url)
        assert job.detail_error == "timeout"
        assert job.detail_error_category == "retriable"


class TestJobRepoScoring:
    def test_update_score(self, repo, sample_job, now):
        repo.upsert(sample_job)
        repo.update_score(
            ScoreResultDTO(
                url=sample_job.url,
                fit_score=8,
                score_reasoning="good match",
                scored_at=now,
            )
        )
        job = repo.get_by_url(sample_job.url)
        assert job.fit_score == 8
        assert job.score_reasoning == "good match"

    def test_update_exclusion(self, repo, sample_job, now):
        repo.upsert(sample_job)
        repo.update_exclusion(
            ExclusionResultDTO(
                url=sample_job.url,
                exclusion_reason_code="TITLE_MISMATCH",
                exclusion_rule_id="rule_1",
                score_reasoning="excluded",
                scored_at=now,
            )
        )
        job = repo.get_by_url(sample_job.url)
        assert job.fit_score == 0
        assert job.exclusion_reason_code == "TITLE_MISMATCH"

    def test_update_score_failure(self, repo, sample_job):
        repo.upsert(sample_job)
        repo.update_score_failure(
            ScoreFailureDTO(
                url=sample_job.url,
                score_error="LLM timeout",
                score_reasoning="LLM timeout",
                score_retry_count=2,
                score_next_retry_at="2026-01-01T00:00:00Z",
            )
        )
        job = repo.get_by_url(sample_job.url)
        assert job.fit_score is None
        assert job.score_error == "LLM timeout"


class TestJobRepoTailoring:
    def test_update_tailoring(self, repo, sample_job, now):
        repo.upsert(sample_job)
        repo.update_tailoring(
            TailorResultDTO(
                url=sample_job.url,
                tailored_resume_path="/tmp/resume.txt",
                tailored_at=now,
            )
        )
        job = repo.get_by_url(sample_job.url)
        assert job.tailored_resume_path == "/tmp/resume.txt"

    def test_update_cover_letter(self, repo, sample_job, now):
        repo.upsert(sample_job)
        repo.update_cover_letter(
            CoverLetterResultDTO(
                url=sample_job.url,
                cover_letter_path="/tmp/cl.txt",
                cover_letter_at=now,
            )
        )
        job = repo.get_by_url(sample_job.url)
        assert job.cover_letter_path == "/tmp/cl.txt"

    def test_increment_attempts(self, repo, sample_job):
        repo.upsert(sample_job)
        repo.increment_attempts(sample_job.url, "tailor_attempts")
        repo.increment_attempts(sample_job.url, "tailor_attempts")
        job = repo.get_by_url(sample_job.url)
        assert job.tailor_attempts == 2

    def test_increment_invalid_column(self, repo, sample_job):
        repo.upsert(sample_job)
        with pytest.raises(ValueError):
            repo.increment_attempts(sample_job.url, "url")  # not allowed


class TestJobRepoApply:
    def test_update_apply_status(self, repo, sample_job, now):
        repo.upsert(sample_job)
        repo.update_apply_status(
            ApplyResultDTO(
                url=sample_job.url,
                apply_status="applied",
                applied_at=now,
            )
        )
        job = repo.get_by_url(sample_job.url)
        assert job.apply_status == "applied"
        assert job.agent_id is None  # always cleared

    def test_release_lock(self, repo, sample_job):
        repo.upsert(sample_job)
        repo.update_apply_status(ApplyResultDTO(url=sample_job.url, apply_status=None))
        job = repo.get_by_url(sample_job.url)
        assert job.apply_status is None

    def test_reset_failed_jobs(self, repo, now):
        for i in range(3):
            repo.upsert(JobDTO(url=f"https://x.com/{i}", title=f"J{i}", site="t", discovered_at=now))
        repo.update_apply_status(ApplyResultDTO(url="https://x.com/0", apply_status="failed"))
        repo.update_apply_status(ApplyResultDTO(url="https://x.com/1", apply_status="applied", applied_at=now))
        repo.update_apply_status(ApplyResultDTO(url="https://x.com/2", apply_status="needs_human"))
        count = repo.reset_failed_jobs()
        assert count == 2  # failed + needs_human, not applied


class TestJobRepoUrlOps:
    def test_update_url(self, repo, sample_job):
        repo.upsert(sample_job)
        assert repo.update_url(sample_job.url, "https://new.com/job")
        assert repo.get_by_url("https://new.com/job") is not None
        assert repo.get_by_url(sample_job.url) is None

    def test_delete(self, repo, sample_job):
        repo.upsert(sample_job)
        repo.delete(sample_job.url)
        assert repo.get_by_url(sample_job.url) is None

    def test_update_application_url(self, repo, sample_job):
        repo.upsert(sample_job)
        repo.update_application_url(sample_job.url, "https://apply.com")
        job = repo.get_by_url(sample_job.url)
        assert job.application_url == "https://apply.com"


class TestJobRepoQueries:
    def test_get_pipeline_counts(self, repo, now):
        repo.upsert(JobDTO(url="https://x.com/1", title="A", site="t", discovered_at=now))
        counts = repo.get_pipeline_counts()
        assert counts["total"] == 1
        assert counts["applied"] == 0

    def test_get_stats(self, repo, now):
        repo.upsert(JobDTO(url="https://x.com/1", title="A", site="t", discovered_at=now))
        stats = repo.get_stats()
        assert stats["total"] == 1
        assert "score_distribution" in stats
        assert "by_site" in stats
        assert "score_funnel" in stats

    def test_get_needs_human(self, repo, now):
        repo.upsert(JobDTO(url="https://x.com/1", title="A", site="t", discovered_at=now))
        repo.update_apply_status(ApplyResultDTO(url="https://x.com/1", apply_status="needs_human"))
        jobs = repo.get_needs_human()
        assert len(jobs) == 1
        assert jobs[0].url == "https://x.com/1"

    def test_get_jobs_by_stage_dict(self, repo, now):
        repo.upsert(
            JobDTO(
                url="https://x.com/1",
                title="A",
                site="t",
                discovered_at=now,
                full_description="JD",
                fit_score=9,
            )
        )
        jobs = repo.get_jobs_by_stage_dict(stage="scored")
        assert len(jobs) == 1
        assert jobs[0].fit_score == 9

    def test_get_jobs_by_stage_dict_with_url_filter(self, repo, now):
        repo.upsert(JobDTO(url="https://x.com/1", title="A", site="t", discovered_at=now, full_description="JD"))
        repo.upsert(JobDTO(url="https://x.com/2", title="B", site="t", discovered_at=now, full_description="JD"))
        jobs = repo.get_jobs_by_stage_dict(stage="enriched", job_url="https://x.com/1")
        assert len(jobs) == 1

    def test_get_jobs_by_stage_dict_pending_tailor(self, repo, now):
        repo.upsert(
            JobDTO(
                url="https://x.com/1",
                title="A",
                site="t",
                discovered_at=now,
                full_description="JD",
                fit_score=8,
            )
        )
        jobs = repo.get_jobs_by_stage_dict(stage="pending_tailor", min_score=7)
        assert len(jobs) == 1


# ── Guardrail Thresholds ────────────────────────────────────────────


class TestGuardrailThresholds:
    def test_defaults(self):
        from applypilot.guardrails.thresholds import _DEFAULTS

        assert "resume_tailoring" in _DEFAULTS
        assert _DEFAULTS["resume_tailoring"].threshold == 0.40

    def test_get_threshold_fallback(self):
        from applypilot.guardrails.thresholds import get_threshold

        t = get_threshold("nonexistent_context")
        assert t.threshold == 0.40  # falls back to resume_tailoring default


# ── ServiceResult ────────────────────────────────────────────────────


class TestServiceResult:
    def test_success(self):
        from applypilot.services.base import ServiceResult

        r = ServiceResult(data={"x": 1})
        assert r.success is True
        assert r.data == {"x": 1}

    def test_failure(self):
        from applypilot.services.base import ServiceResult

        r = ServiceResult(success=False, error="boom")
        assert r.success is False
        assert r.error == "boom"


# ── Pieces Decomposer ───────────────────────────────────────────────


class TestDecomposer:
    def test_decompose_basic(self, conn):
        from applypilot.db.sqlite.piece_repo import SqlitePieceRepository
        from applypilot.tailoring.pieces.decomposer import decompose_to_pieces

        repo = SqlitePieceRepository(conn)
        resume = {
            "basics": {"name": "Alice", "label": "Engineer"},
            "work": [{"name": "Acme", "position": "SDE", "highlights": ["Built X", "Led Y"]}],
            "skills": [{"name": "Languages", "keywords": ["Python", "Go"]}],
            "education": [{"institution": "MIT", "studyType": "BS", "area": "CS"}],
        }
        pieces = decompose_to_pieces(resume, repo)
        types = [p.piece_type for p in pieces]
        assert "header" in types
        assert "experience_entry" in types
        assert "bullet" in types
        assert "skill_group" in types
        assert "education" in types
        assert types.count("bullet") == 2

    def test_decompose_dedup(self, conn):
        from applypilot.db.sqlite.piece_repo import SqlitePieceRepository
        from applypilot.tailoring.pieces.decomposer import decompose_to_pieces

        repo = SqlitePieceRepository(conn)
        resume = {"basics": {"name": "Alice"}, "work": [], "skills": [], "education": []}
        p1 = decompose_to_pieces(resume, repo)
        p2 = decompose_to_pieces(resume, repo)
        # Same content_hash → reuses existing pieces
        assert len(p1) == len(p2)
        assert p1[0].content_hash == p2[0].content_hash


# ── Reassembler ──────────────────────────────────────────────────────


class TestReassembler:
    def test_assemble_from_pieces(self):
        from applypilot.db.dto import PieceDTO
        from applypilot.tailoring.pieces.reassembler import assemble_from_pieces

        pieces = [
            PieceDTO(id="h", content_hash="h", piece_type="header", content="Alice\nEngineer", sort_order=0),
            PieceDTO(id="e1", content_hash="e1", piece_type="experience_entry", content="SDE at Acme", sort_order=10),
            PieceDTO(
                id="b1", content_hash="b1", piece_type="bullet", content="Built X", parent_piece_id="e1", sort_order=0
            ),
        ]
        text = assemble_from_pieces(pieces)
        assert "Alice" in text
        assert "EXPERIENCE" in text
        assert "- Built X" in text
