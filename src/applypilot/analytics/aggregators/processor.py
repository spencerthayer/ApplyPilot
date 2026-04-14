"""Processor."""

from __future__ import annotations

import json
import logging

from applypilot.analytics.aggregators.models import (
    CareerHealthReport,
    CareerRoadmapReport,
    EffectivenessReport,
    LatencyReport,
    MarketIntelligenceReport,
    PoolSegmentationReport,
    SkillGapReport,
    TailoringReport,
    TrackComparisonReport,
)

log = logging.getLogger(__name__)


def process_event(
        event_type: str,
        payload_str: str,
        *,
        skill_gaps: SkillGapReport,
        effectiveness: EffectivenessReport,
        pool: PoolSegmentationReport,
        market: MarketIntelligenceReport | None = None,
        health: CareerHealthReport | None = None,
        roadmap: CareerRoadmapReport | None = None,
        tracks: TrackComparisonReport | None = None,
        tailoring: TailoringReport | None = None,
        latency: LatencyReport | None = None,
) -> None:
    """Route an analytics event to the appropriate aggregator."""
    try:
        payload = json.loads(payload_str) if isinstance(payload_str, str) else payload_str
    except (json.JSONDecodeError, TypeError):
        return

    match event_type:
        case "job_scored":
            skill_gaps.ingest(payload)
            pool.ingest(payload)
            if market:
                market.ingest(payload)
            if health:
                health.ingest_score(payload)
            if roadmap:
                roadmap.ingest(payload)
            if tracks:
                tracks.ingest(payload)
        case "job_applied" | "apply_failed" | "apply_needs_human":
            effectiveness.ingest(payload)
            if health:
                health.ingest_apply(payload)
        case "job_tailored":
            if tailoring:
                tailoring.ingest(payload)
            if tracks:
                tracks.ingest(payload)
        case "cache_hit":
            pass  # tracked via analytics_events table, no aggregator yet
        case "track_selected":
            if tracks:
                tracks.ingest(payload)
        case "llm_call":
            if latency and (d := payload.get("duration_ms")):
                latency.ingest_llm(d)
        case "stage_completed":
            if latency and (d := payload.get("duration_ms")):
                latency.ingest_stage(payload.get("stage", "unknown"), d)
        case "job_discovered":
            pool.ingest(payload)
            if tracks:
                tracks.ingest(payload)
        case "job_enriched":
            if latency and (d := payload.get("elapsed_s")):
                latency.ingest_stage("enrich", int(d * 1000))


def generate_summary(
        skill_gaps: SkillGapReport,
        effectiveness: EffectivenessReport,
        pool: PoolSegmentationReport,
        market: MarketIntelligenceReport | None = None,
        health: CareerHealthReport | None = None,
        roadmap: CareerRoadmapReport | None = None,
        tracks: TrackComparisonReport | None = None,
        tailoring: TailoringReport | None = None,
        latency: LatencyReport | None = None,
) -> dict:
    """Generate a combined analytics summary."""
    result = {
        "skill_gaps": {
            "top_missing": skill_gaps.top(15),
            "jobs_analyzed": skill_gaps.total_jobs_analyzed,
        },
        "effectiveness": {
            "by_tier": {k: dict(v) for k, v in effectiveness.by_tier.items()},
            "by_site": {k: dict(v) for k, v in effectiveness.by_site.items()},
            "success_rate_by_tier": effectiveness.success_rate(effectiveness.by_tier),
        },
        "pool": {
            "total": pool.total,
            "by_site": pool.by_site.most_common(20),
            "by_score_band": dict(pool.by_score_band),
            "by_location": pool.by_location.most_common(15),
        },
    }
    if market:
        result["market"] = {
            "total_jobs": market.total_jobs,
            "top_skills": market.top_skills(20),
            "top_locations": market.top_locations(10),
            "seniority_distribution": dict(market.seniority_levels),
        }
    if health:
        result["career_health"] = {
            "score": health.compute_score(),
            "skill_data_points": len(health.matched_skill_counts),
            "apply_outcomes": dict(health.apply_outcomes),
        }
    if roadmap:
        result["roadmap"] = {
            "milestones": roadmap.generate_milestones(),
            "strengths": roadmap.strengths(),
            "total_jobs": roadmap.total_jobs,
        }
    if tracks:
        result["track_comparison"] = tracks.compare()
    if tailoring:
        result["tailoring"] = tailoring.summary()
    if latency:
        result["latency"] = latency.summary()
    return result
