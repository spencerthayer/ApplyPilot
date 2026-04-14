"""Tests for tailoring analytics events."""

from applypilot.analytics.aggregators.processor import process_event
from applypilot.analytics.aggregators.models import (
    SkillGapReport,
    EffectivenessReport,
    PoolSegmentationReport,
    TailoringReport,
    TrackComparisonReport,
)


def _make_reports():
    return {
        "skill_gaps": SkillGapReport(),
        "effectiveness": EffectivenessReport(),
        "pool": PoolSegmentationReport(),
        "tracks": TrackComparisonReport(),
        "tailoring": TailoringReport(),
    }


def test_job_scored_routes_to_skill_gaps():
    r = _make_reports()
    process_event(
        "job_scored", '{"url":"x","fit_score":7,"matched_skills":["Python"],"missing_requirements":["React"]}', **r
    )
    assert r["skill_gaps"].total_jobs_analyzed == 1


def test_job_tailored_routes_to_tracks():
    r = _make_reports()
    r["tailoring"] = TailoringReport()
    process_event("job_tailored", '{"url":"x","pipeline":"two_stage","track_id":"backend","overlay_count":5}', **r)
    assert r["tailoring"].total_jobs == 1
    assert r["tailoring"].by_pipeline["two_stage"] == 1


def test_tailoring_report_cache_hit_rate():
    tr = TailoringReport()
    tr.ingest({"pipeline": "two_stage", "overlay_count": 5})
    tr.ingest({"pipeline": "cache_hit", "overlay_count": 0})
    tr.ingest({"pipeline": "two_stage", "overlay_count": 3})
    s = tr.summary()
    assert s["total_jobs"] == 3
    assert s["cache_hit_rate"] == 0.33
    assert s["total_overlays"] == 8


def test_bullet_effectiveness_report():
    from applypilot.analytics.aggregators.models import BulletEffectivenessReport

    br = BulletEffectivenessReport()
    br.ingest("b1", "applied")
    br.ingest("b1", "applied")
    br.ingest("b1", "rejected")
    br.ingest("b2", "rejected")
    top = br.top_performers()
    assert top[0]["bullet_id"] == "b1"
    assert top[0]["rate"] == 0.67


def test_track_selected_routes_to_tracks():
    r = _make_reports()
    process_event("track_selected", '{"url":"x","track_id":"backend","fit_score":7}', **r)


def test_unknown_event_does_not_crash():
    r = _make_reports()
    process_event("unknown_event", '{"foo":"bar"}', **r)
