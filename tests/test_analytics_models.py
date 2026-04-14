"""Tests for analytics aggregator models."""

import pytest
from applypilot.analytics.aggregators.models import (
    CareerHealthReport,
    CareerRoadmapReport,
    EffectivenessReport,
    MarketIntelligenceReport,
    PoolSegmentationReport,
    SkillGapReport,
    TrackComparisonReport,
)
from applypilot.analytics.aggregators.processor import process_event, generate_summary


class TestSkillGapReport:
    def test_ingest_and_top(self):
        r = SkillGapReport()
        r.ingest({"missing_requirements": ["Python", "Go", "Python"]})
        r.ingest({"missing_requirements": ["Python", "Rust"]})
        top = r.top(2)
        assert top[0] == ("python", 3)
        assert r.total_jobs_analyzed == 2

    def test_empty(self):
        r = SkillGapReport()
        assert r.top(5) == []


class TestEffectivenessReport:
    def test_ingest_and_rate(self):
        r = EffectivenessReport()
        r.ingest({"tier": "T1", "site": "indeed", "status": "applied"})
        r.ingest({"tier": "T1", "site": "indeed", "status": "failed"})
        rates = r.success_rate(r.by_tier)
        assert rates["T1"] == 0.5


class TestPoolSegmentation:
    def test_score_bands(self):
        r = PoolSegmentationReport()
        r.ingest({"site": "indeed", "fit_score": 9, "location": "SF"})
        r.ingest({"site": "indeed", "fit_score": 3, "location": "NY"})
        assert r.by_score_band["9-10 (strong)"] == 1
        assert r.by_score_band["1-4 (skip)"] == 1
        assert r.total == 2


class TestMarketIntelligence:
    def test_ingest(self):
        r = MarketIntelligenceReport()
        r.ingest(
            {
                "matched_skills": ["Python"],
                "missing_requirements": ["Go"],
                "salary": "120k",
                "location": "Remote",
                "seniority": "senior",
            }
        )
        assert r.total_jobs == 1
        assert ("python", 1) in r.top_skills(5)
        assert ("go", 1) in r.top_skills(5)
        assert len(r.salary_mentions) == 1


class TestCareerHealth:
    def test_compute_score(self):
        r = CareerHealthReport()
        for _ in range(10):
            r.ingest_score({"matched_skills": ["a", "b"], "missing_requirements": ["c"], "fit_score": 8})
        r.ingest_apply({"status": "applied"})
        r.ingest_apply({"status": "failed"})
        score = r.compute_score()
        assert 0 <= score <= 10

    def test_empty_score(self):
        assert CareerHealthReport().compute_score() == 0.0


class TestCareerRoadmap:
    def test_milestones(self):
        r = CareerRoadmapReport()
        for _ in range(5):
            r.ingest({"missing_requirements": ["Kubernetes", "Go"], "matched_skills": ["Python"]})
        ms = r.generate_milestones(2)
        assert len(ms) == 2
        assert ms[0]["skill"] in ("kubernetes", "go")
        assert ms[0]["priority"] == "high"

    def test_strengths(self):
        r = CareerRoadmapReport()
        r.ingest({"missing_requirements": [], "matched_skills": ["Python", "Python"]})
        assert r.strengths(1)[0][0] == "python"


class TestTrackComparison:
    def test_compare(self):
        r = TrackComparisonReport()
        r.ingest({"site": "indeed", "fit_score": 9})
        r.ingest({"site": "indeed", "fit_score": 7})
        r.ingest({"site": "linkedin", "fit_score": 3})
        result = r.compare()
        assert result[0]["segment"] == "indeed"
        assert result[0]["avg_score"] == 8.0


class TestProcessEvent:
    def test_routes_job_scored(self):
        sg = SkillGapReport()
        eff = EffectivenessReport()
        pool = PoolSegmentationReport()
        mkt = MarketIntelligenceReport()
        health = CareerHealthReport()
        roadmap = CareerRoadmapReport()
        tracks = TrackComparisonReport()

        process_event(
            "job_scored",
            '{"fit_score": 8, "matched_skills": ["Python"], "missing_requirements": ["Go"], "site": "indeed", "location": "SF"}',
            skill_gaps=sg,
            effectiveness=eff,
            pool=pool,
            market=mkt,
            health=health,
            roadmap=roadmap,
            tracks=tracks,
        )

        assert sg.total_jobs_analyzed == 1
        assert mkt.total_jobs == 1
        assert pool.total == 1

    def test_routes_apply(self):
        sg = SkillGapReport()
        eff = EffectivenessReport()
        pool = PoolSegmentationReport()
        health = CareerHealthReport()

        process_event(
            "job_applied",
            '{"tier": "T1", "site": "indeed", "status": "applied"}',
            skill_gaps=sg,
            effectiveness=eff,
            pool=pool,
            health=health,
        )

        assert eff.by_tier["T1"]["applied"] == 1

    def test_bad_json_ignored(self):
        sg = SkillGapReport()
        eff = EffectivenessReport()
        pool = PoolSegmentationReport()
        process_event("job_scored", "not json", skill_gaps=sg, effectiveness=eff, pool=pool)
        assert sg.total_jobs_analyzed == 0


class TestGenerateSummary:
    def test_full_summary(self):
        sg = SkillGapReport()
        eff = EffectivenessReport()
        pool = PoolSegmentationReport()
        mkt = MarketIntelligenceReport()
        health = CareerHealthReport()
        roadmap = CareerRoadmapReport()
        tracks = TrackComparisonReport()

        summary = generate_summary(sg, eff, pool, market=mkt, health=health, roadmap=roadmap, tracks=tracks)
        assert "skill_gaps" in summary
        assert "market" in summary
        assert "career_health" in summary
        assert "roadmap" in summary
        assert "track_comparison" in summary
