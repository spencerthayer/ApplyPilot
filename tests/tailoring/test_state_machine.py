"""Tests for the smart tailoring state machine, bullet bank, and quality gates."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from applypilot.tailoring.models import (
    Bullet,
    BulletVariant,
    GateResult,
    Resume,
    TailoringResult,
)
from applypilot.tailoring.bullet_bank import BulletBank
from applypilot.tailoring.quality_gates import MetricsGate, RelevanceGate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path):
    """Return a path to a temporary SQLite database."""
    return str(tmp_path / "test_bullets.db")


@pytest.fixture
def bullet_bank(tmp_db):
    """Return a fresh BulletBank instance backed by a repo."""
    import sqlite3
    from applypilot.db.schema import init_db
    from applypilot.db.sqlite.bullet_bank_repo import SqliteBulletBankRepository

    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    repo = SqliteBulletBankRepository(conn)
    return BulletBank(repo)


@pytest.fixture
def sample_resume():
    """Resume with experience bullets containing metrics."""
    return Resume(
        text="Led team of 12 engineers. Increased revenue by 40%. Deployed 3 microservices.",
        sections={
            "EXPERIENCE": {
                "bullets": [
                    "Led team of 12 engineers to deliver platform migration",
                    "Increased revenue by 40% through optimization",
                    "Deployed 3 microservices reducing latency by 60%",
                    "Managed $2M annual budget",
                    "Wrote documentation for internal tools",
                ]
            },
            "SKILLS": ["Python", "AWS", "Docker"],
        },
    )


@pytest.fixture
def sample_resume_no_metrics():
    """Resume with bullets that lack quantifiable metrics."""
    return Resume(
        text="Led engineering team. Improved performance. Wrote code.",
        sections={
            "EXPERIENCE": {
                "bullets": [
                    "Led engineering team",
                    "Improved system performance",
                    "Wrote backend code",
                    "Participated in code reviews",
                ]
            }
        },
    )


@pytest.fixture
def sample_job():
    """A sample job dict."""
    return {
        "title": "Senior Software Engineer",
        "company": "TechCorp",
        "description": (
            "We are looking for a Senior Software Engineer to lead backend development. "
            "Requirements: 5+ years Python, experience with microservices, AWS. "
            "Nice to have: Kubernetes, CI/CD."
        ),
    }


@pytest.fixture
def mock_llm_client():
    """Mock LLM client that returns predictable responses."""
    client = MagicMock()
    client.ask.return_value = "Increased platform revenue by 45% through automated optimization pipeline"
    client.chat.return_value = "8"
    return client


# ---------------------------------------------------------------------------
# Models tests
# ---------------------------------------------------------------------------


class TestModels:
    def test_bullet_creation(self):
        from datetime import datetime

        b = Bullet(
            id="test-1",
            text="Led team of 12",
            context={"role": "eng"},
            tags=["leadership"],
            metrics=["12"],
            created_at=datetime.now(),
        )
        assert b.id == "test-1"
        assert b.use_count == 0
        assert b.success_rate == 0.0

    def test_bullet_variant(self):
        v = BulletVariant(original_bullet_id="b1", text="Improved X by 50%", strategy="quantify")
        assert v.score is None

    def test_gate_result_defaults(self):
        r = GateResult(passed=True, score=0.9, feedback="OK")
        assert r.retry_prompt is None

    def test_resume_defaults(self):
        r = Resume(text="hello")
        assert r.sections == {}

    def test_tailoring_result(self):
        r = TailoringResult(resume=Resume(text="x"), score=0.8, iterations=2)
        assert r.quality_results == []


# ---------------------------------------------------------------------------
# BulletBank tests
# ---------------------------------------------------------------------------


class TestBulletBank:
    def test_add_and_get_bullet(self, bullet_bank):
        bullet = bullet_bank.add_bullet(
            text="Led team of 12 engineers",
            context={"role": "lead"},
            tags=["leadership"],
            metrics=["12"],
        )
        assert bullet.id is not None
        assert bullet.text == "Led team of 12 engineers"

        retrieved = bullet_bank.get_bullet(bullet.id)
        assert retrieved is not None
        assert retrieved.text == bullet.text

    def test_get_bullet_not_found(self, bullet_bank):
        assert bullet_bank.get_bullet("nonexistent") is None

    def test_get_variants_all(self, bullet_bank):
        bullet_bank.add_bullet("Bullet A", tags=["python"])
        bullet_bank.add_bullet("Bullet B", tags=["aws"])
        bullets = bullet_bank.get_variants()
        assert len(bullets) == 2

    def test_get_variants_filtered_by_tag(self, bullet_bank):
        bullet_bank.add_bullet("Bullet A", tags=["python"])
        bullet_bank.add_bullet("Bullet B", tags=["aws"])
        bullets = bullet_bank.get_variants(tags=["python"])
        assert len(bullets) == 1
        assert bullets[0].text == "Bullet A"

    def test_record_feedback(self, bullet_bank):
        bullet = bullet_bank.add_bullet("Test bullet", tags=["test"])
        bullet_bank.record_feedback(bullet.id, "SWE at BigCo", "selected")
        updated = bullet_bank.get_bullet(bullet.id)
        assert updated.use_count == 1
        assert updated.success_rate == 1.0

    def test_record_feedback_mixed(self, bullet_bank):
        bullet = bullet_bank.add_bullet("Test bullet", tags=["test"])
        bullet_bank.record_feedback(bullet.id, "Job A", "selected")
        bullet_bank.record_feedback(bullet.id, "Job B", "rejected")
        updated = bullet_bank.get_bullet(bullet.id)
        assert updated.use_count == 2
        # success_rate only recalculated on "selected" outcome
        assert updated.success_rate == 0.5


# ---------------------------------------------------------------------------
# Quality gates tests
# ---------------------------------------------------------------------------


class TestMetricsGate:
    def test_passes_with_metrics(self, sample_resume):
        gate = MetricsGate()
        result = gate.check(sample_resume, {})
        # 4 out of 5 bullets have metrics (80%) > 70% threshold
        assert result.passed is True
        assert result.score >= 0.7

    def test_fails_without_metrics(self, sample_resume_no_metrics):
        gate = MetricsGate()
        result = gate.check(sample_resume_no_metrics, {})
        assert result.passed is False
        assert result.score < 0.7
        assert result.retry_prompt is not None

    def test_fails_with_no_bullets(self):
        gate = MetricsGate()
        empty_resume = Resume(text="", sections={})
        result = gate.check(empty_resume, {})
        assert result.passed is False
        assert result.score == 0.0


class TestRelevanceGate:
    @patch("applypilot.tailoring.quality_gates.get_client")
    def test_passes_with_high_score(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.ask.return_value = "8"
        mock_get_client.return_value = mock_client

        gate = RelevanceGate()
        job_intel = MagicMock()
        job_intel.title = "Senior SWE"
        job_intel.company = "TechCorp"

        resume = Resume(text="Python expert with 10 years experience", sections={})
        result = gate.check(resume, {"job_intelligence": job_intel})
        assert result.passed is True
        assert result.score == 0.8

    @patch("applypilot.tailoring.quality_gates.get_client")
    def test_fails_with_low_score(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.ask.return_value = "3"
        mock_get_client.return_value = mock_client

        gate = RelevanceGate()
        job_intel = MagicMock()
        job_intel.title = "Chef"
        job_intel.company = "Restaurant"

        resume = Resume(text="Python developer", sections={})
        result = gate.check(resume, {"job_intelligence": job_intel})
        assert result.passed is False
        assert result.score == 0.3

    @patch("applypilot.tailoring.quality_gates.get_client")
    def test_skips_without_job_intel(self, mock_get_client):
        mock_get_client.return_value = MagicMock()
        gate = RelevanceGate()
        resume = Resume(text="anything", sections={})
        result = gate.check(resume, {})
        assert result.passed is True
        assert result.score == 1.0


# ---------------------------------------------------------------------------
# State machine tests
# ---------------------------------------------------------------------------


class TestSmartTailoringEngine:
    @patch("applypilot.tailoring.quality_gates.get_client")
    @patch("applypilot.tailoring.state_machine.get_client")
    @patch("applypilot.tailoring.state_machine.ResumeMatcher")
    @patch("applypilot.tailoring.state_machine.JobDescriptionParser")
    def test_initial_state(self, mock_parser, mock_matcher, mock_get_client, mock_gate_client, tmp_path):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_gate_client.return_value = mock_client

        from applypilot.tailoring.state_machine import SmartTailoringEngine

        engine = SmartTailoringEngine({"bullet_bank_path": str(tmp_path / "test.db")})
        assert engine.state == "ANALYZE"

    @patch("applypilot.tailoring.quality_gates.get_client")
    @patch("applypilot.tailoring.state_machine.get_client")
    @patch("applypilot.tailoring.state_machine.ResumeMatcher")
    @patch("applypilot.tailoring.state_machine.JobDescriptionParser")
    def test_analyze_to_extract_transition(
        self, mock_parser_cls, mock_matcher_cls, mock_get_client, mock_gate_client, tmp_path
    ):
        mock_client = MagicMock()
        mock_client.ask.return_value = "Improved revenue by 50% through automation"
        mock_get_client.return_value = mock_client
        mock_gate_client.return_value = mock_client

        # Setup mock parser and matcher
        mock_parser = MagicMock()
        mock_intel = MagicMock()
        mock_intel.title = "SWE"
        mock_intel.company = "Corp"
        mock_parser.parse.return_value = mock_intel
        mock_parser_cls.return_value = mock_parser

        mock_matcher = MagicMock()
        mock_analysis = MagicMock()
        mock_analysis.overall_score = 8.0
        mock_matcher.analyze.return_value = mock_analysis
        mock_matcher_cls.return_value = mock_matcher

        from applypilot.tailoring.state_machine import SmartTailoringEngine

        engine = SmartTailoringEngine(
            {
                "bullet_bank_path": str(tmp_path / "test.db"),
                "max_iterations": 10,
            }
        )

        # The on_enter callbacks auto-chain, but we need to verify
        # the state transitions are defined correctly
        assert engine.state == "ANALYZE"
        # Manually test transition
        engine.analyze_complete()
        assert engine.state == "EXTRACT"

    @patch("applypilot.tailoring.quality_gates.get_client")
    @patch("applypilot.tailoring.state_machine.get_client")
    @patch("applypilot.tailoring.state_machine.ResumeMatcher")
    @patch("applypilot.tailoring.state_machine.JobDescriptionParser")
    def test_all_transitions_defined(self, mock_parser, mock_matcher, mock_get_client, mock_gate_client, tmp_path):
        """Verify all 9 states and transitions exist."""
        mock_get_client.return_value = MagicMock()
        mock_gate_client.return_value = MagicMock()

        from applypilot.tailoring.state_machine import SmartTailoringEngine

        engine = SmartTailoringEngine({"bullet_bank_path": str(tmp_path / "test.db")})

        assert len(SmartTailoringEngine.states) == 9
        assert "ANALYZE" in SmartTailoringEngine.states
        assert "EXTRACT" in SmartTailoringEngine.states
        assert "GENERATE" in SmartTailoringEngine.states
        assert "SCORE" in SmartTailoringEngine.states
        assert "SELECT" in SmartTailoringEngine.states
        assert "VALIDATE" in SmartTailoringEngine.states
        assert "JUDGE" in SmartTailoringEngine.states
        assert "ASSEMBLE" in SmartTailoringEngine.states
        assert "LEARN" in SmartTailoringEngine.states

    @patch("applypilot.tailoring.quality_gates.get_client")
    @patch("applypilot.tailoring.state_machine.get_client")
    @patch("applypilot.tailoring.state_machine.ResumeMatcher")
    @patch("applypilot.tailoring.state_machine.JobDescriptionParser")
    def test_full_happy_path(
        self, mock_parser_cls, mock_matcher_cls, mock_get_client, mock_gate_client, tmp_path, sample_resume, sample_job
    ):
        """Test the full pipeline from ANALYZE to LEARN."""
        mock_client = MagicMock()
        # LLM returns bullet with metrics
        mock_client.ask.return_value = "Increased platform throughput by 300% serving 10M daily requests"
        mock_get_client.return_value = mock_client
        mock_gate_client.return_value = mock_client

        mock_parser = MagicMock()
        mock_intel = MagicMock()
        mock_intel.title = "Senior SWE"
        mock_intel.company = "TechCorp"
        mock_parser.parse.return_value = mock_intel
        mock_parser_cls.return_value = mock_parser

        mock_matcher = MagicMock()
        mock_analysis = MagicMock()
        mock_analysis.overall_score = 8.5
        mock_matcher.analyze.return_value = mock_analysis
        mock_matcher_cls.return_value = mock_matcher

        from applypilot.tailoring.state_machine import SmartTailoringEngine

        engine = SmartTailoringEngine(
            {
                "bullet_bank_path": str(tmp_path / "test.db"),
                "max_iterations": 10,
            }
        )

        result = engine.run(sample_job, sample_resume)

        assert isinstance(result, TailoringResult)
        assert result.resume is not None
        assert result.iterations >= 1
        assert engine._done is True

    @patch("applypilot.tailoring.quality_gates.get_client")
    @patch("applypilot.tailoring.state_machine.get_client")
    @patch("applypilot.tailoring.state_machine.ResumeMatcher")
    @patch("applypilot.tailoring.state_machine.JobDescriptionParser")
    def test_max_iterations_respected(
        self,
        mock_parser_cls,
        mock_matcher_cls,
        mock_get_client,
        mock_gate_client,
        tmp_path,
        sample_resume_no_metrics,
        sample_job,
    ):
        """Test that max_iterations prevents infinite loops."""
        mock_client = MagicMock()
        # Return text WITHOUT metrics so quality gates fail → loops
        mock_client.ask.return_value = "Improved system performance significantly"
        mock_get_client.return_value = mock_client
        mock_gate_client.return_value = mock_client

        mock_parser = MagicMock()
        mock_intel = MagicMock()
        mock_intel.title = "SWE"
        mock_intel.company = "Corp"
        mock_parser.parse.return_value = mock_intel
        mock_parser_cls.return_value = mock_parser

        mock_matcher = MagicMock()
        mock_analysis = MagicMock()
        mock_analysis.overall_score = 5.0
        mock_matcher.analyze.return_value = mock_analysis
        mock_matcher_cls.return_value = mock_matcher

        from applypilot.tailoring.state_machine import SmartTailoringEngine

        engine = SmartTailoringEngine(
            {
                "bullet_bank_path": str(tmp_path / "test.db"),
                "max_iterations": 2,
            }
        )

        result = engine.run(sample_job, sample_resume_no_metrics)
        assert result.iterations <= 2

    @patch("applypilot.tailoring.quality_gates.get_client")
    @patch("applypilot.tailoring.state_machine.get_client")
    @patch("applypilot.tailoring.state_machine.ResumeMatcher")
    @patch("applypilot.tailoring.state_machine.JobDescriptionParser")
    def test_score_variants(self, mock_parser, mock_matcher, mock_get_client, mock_gate_client, tmp_path):
        """Test that scoring assigns scores based on heuristics."""
        mock_get_client.return_value = MagicMock()
        mock_gate_client.return_value = MagicMock()

        from applypilot.tailoring.state_machine import SmartTailoringEngine

        engine = SmartTailoringEngine({"bullet_bank_path": str(tmp_path / "test.db")})

        engine.generated_variants = [
            BulletVariant("", "Increased revenue by 50% through optimization efforts in Q3", "quantify"),
            BulletVariant("", "Did stuff", "quantify"),
        ]
        engine._score_variants()

        # First has numbers and good length → higher score
        assert engine.generated_variants[0].score > engine.generated_variants[1].score

    @patch("applypilot.tailoring.quality_gates.get_client")
    @patch("applypilot.tailoring.state_machine.get_client")
    @patch("applypilot.tailoring.state_machine.ResumeMatcher")
    @patch("applypilot.tailoring.state_machine.JobDescriptionParser")
    def test_select_best_variants(self, mock_parser, mock_matcher, mock_get_client, mock_gate_client, tmp_path):
        """Test that selection picks top N by score."""
        mock_get_client.return_value = MagicMock()
        mock_gate_client.return_value = MagicMock()

        from applypilot.tailoring.state_machine import SmartTailoringEngine

        engine = SmartTailoringEngine(
            {
                "bullet_bank_path": str(tmp_path / "test.db"),
                "max_bullets": 2,
            }
        )

        engine.generated_variants = [
            BulletVariant("", "Low", "q", score=0.2),
            BulletVariant("", "High", "q", score=0.9),
            BulletVariant("", "Mid", "q", score=0.5),
        ]
        engine._select_best_variants()

        assert len(engine.selected_bullets) == 2
        assert engine.selected_bullets[0].text == "High"
        assert engine.selected_bullets[1].text == "Mid"

    @patch("applypilot.tailoring.quality_gates.get_client")
    @patch("applypilot.tailoring.state_machine.get_client")
    @patch("applypilot.tailoring.state_machine.ResumeMatcher")
    @patch("applypilot.tailoring.state_machine.JobDescriptionParser")
    def test_assemble_resume_preserves_sections(
        self, mock_parser, mock_matcher, mock_get_client, mock_gate_client, tmp_path, sample_resume
    ):
        """Test that assembly preserves non-EXPERIENCE sections."""
        mock_get_client.return_value = MagicMock()
        mock_gate_client.return_value = MagicMock()

        from applypilot.tailoring.state_machine import SmartTailoringEngine

        engine = SmartTailoringEngine({"bullet_bank_path": str(tmp_path / "test.db")})
        engine.current_resume = sample_resume
        engine.selected_bullets = [
            BulletVariant("", "New bullet 1", "q", score=0.9),
        ]

        assembled = engine._assemble_resume()
        assert "SKILLS" in assembled.sections
        assert assembled.sections["SKILLS"] == ["Python", "AWS", "Docker"]
        assert "EXPERIENCE" in assembled.sections

    @patch("applypilot.tailoring.quality_gates.get_client")
    @patch("applypilot.tailoring.state_machine.get_client")
    @patch("applypilot.tailoring.state_machine.ResumeMatcher")
    @patch("applypilot.tailoring.state_machine.JobDescriptionParser")
    def test_extract_achievements_handles_list_format(
        self, mock_parser, mock_matcher, mock_get_client, mock_gate_client, tmp_path
    ):
        """Test extraction handles EXPERIENCE as a list."""
        mock_get_client.return_value = MagicMock()
        mock_gate_client.return_value = MagicMock()

        from applypilot.tailoring.state_machine import SmartTailoringEngine

        engine = SmartTailoringEngine({"bullet_bank_path": str(tmp_path / "test.db")})

        resume = Resume(text="", sections={"EXPERIENCE": ["bullet 1", "bullet 2"]})
        achievements = engine._extract_achievements(resume)
        assert achievements == ["bullet 1", "bullet 2"]

    @patch("applypilot.tailoring.quality_gates.get_client")
    @patch("applypilot.tailoring.state_machine.get_client")
    @patch("applypilot.tailoring.state_machine.ResumeMatcher")
    @patch("applypilot.tailoring.state_machine.JobDescriptionParser")
    def test_extract_achievements_handles_none(
        self, mock_parser, mock_matcher, mock_get_client, mock_gate_client, tmp_path
    ):
        """Test extraction returns empty list for None resume."""
        mock_get_client.return_value = MagicMock()
        mock_gate_client.return_value = MagicMock()

        from applypilot.tailoring.state_machine import SmartTailoringEngine

        engine = SmartTailoringEngine({"bullet_bank_path": str(tmp_path / "test.db")})
        assert engine._extract_achievements(None) == []
