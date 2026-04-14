"""Analytics event helpers — emit events from pipeline stages.

Each helper builds the payload and calls emit(). Import these in pipeline
stages instead of constructing events manually.
"""

from __future__ import annotations

import json


def _emit(stage: str, event_type: str, payload: dict) -> None:
    """Best-effort emit — never blocks, never crashes the pipeline."""
    try:
        from applypilot.bootstrap import get_app
        from applypilot.analytics.events import emit

        emit(stage, event_type, json.dumps(payload), get_app().container.analytics_repo)
    except Exception:
        pass  # Analytics is a parallel observer — never blocks


def emit_job_discovered(url: str, site: str, title: str) -> None:
    _emit("discover", "job_discovered", {"url": url, "site": site, "title": title})


def emit_job_scored(
        url: str,
        site: str,
        fit_score: int,
        matched_skills: list[str] | None = None,
        missing_requirements: list[str] | None = None,
        level_strategy: str | None = None,
        location: str | None = None,
) -> None:
    _emit(
        "score",
        "job_scored",
        {
            "url": url,
            "site": site,
            "fit_score": fit_score,
            "matched_skills": matched_skills or [],
            "missing_requirements": missing_requirements or [],
            "level_strategy": level_strategy or "",
            "location": location,
        },
    )


def emit_job_applied(url: str, site: str, tier: str, duration_ms: int | None = None) -> None:
    _emit(
        "apply",
        "job_applied",
        {
            "url": url,
            "site": site,
            "tier": tier,
            "status": "applied",
            "duration_ms": duration_ms,
        },
    )


def emit_apply_failed(url: str, site: str, tier: str, error: str) -> None:
    _emit(
        "apply",
        "apply_failed",
        {
            "url": url,
            "site": site,
            "tier": tier,
            "status": "failed",
            "error": error,
        },
    )


def emit_apply_needs_human(url: str, site: str, tier: str, reason: str) -> None:
    _emit(
        "apply",
        "apply_needs_human",
        {
            "url": url,
            "site": site,
            "tier": tier,
            "status": "needs_human",
            "reason": reason,
        },
    )


def emit_job_tailored(
        url: str,
        pipeline: str,
        track_id: str | None = None,
        plan_requirements: int = 0,
        plan_gaps: int = 0,
        overlay_count: int = 0,
) -> None:
    _emit(
        "tailor",
        "job_tailored",
        {
            "url": url,
            "pipeline": pipeline,
            "track_id": track_id or "",
            "plan_requirements": plan_requirements,
            "plan_gaps": plan_gaps,
            "overlay_count": overlay_count,
        },
    )


def emit_cache_hit(url: str, track_id: str | None = None, overlay_count: int = 0) -> None:
    _emit("tailor", "cache_hit", {"url": url, "track_id": track_id or "", "overlay_count": overlay_count})


def emit_pieces_decomposed(piece_count: int, bullet_count: int) -> None:
    _emit("tailor", "pieces_decomposed", {"piece_count": piece_count, "bullet_count": bullet_count})


def emit_track_selected(url: str, track_id: str, score: int | None = None) -> None:
    _emit("score", "track_selected", {"url": url, "track_id": track_id, "fit_score": score})


def emit_job_enriched(url: str, site: str, tier: int, desc_length: int, elapsed_s: float, status: str) -> None:
    _emit(
        "enrich",
        "job_enriched",
        {
            "url": url,
            "site": site,
            "tier": tier,
            "desc_length": desc_length,
            "elapsed_s": round(elapsed_s, 1),
            "status": status,
        },
    )
