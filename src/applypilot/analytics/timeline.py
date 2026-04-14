"""Job timeline — full lifecycle view for a single job URL.

Usage:
    applypilot timeline <URL>
    applypilot timeline <URL> --json

Shows every stage transition, timing, errors, LLM calls, and apply attempts
for one job in chronological order.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


def get_job_timeline(url: str) -> dict | None:
    """Pull the complete lifecycle for a job URL.

    Returns a dict with:
        job: core job data
        timeline: list of {timestamp, stage, event, detail} in chronological order
        files: list of artifact paths
        stats: summary metrics
    """
    from applypilot.db.connection import get_connection

    conn = get_connection()
    conn.row_factory = sqlite3.Row

    # 1. Job data
    row = conn.execute("SELECT * FROM jobs WHERE url = ? OR application_url = ?", (url, url)).fetchone()
    if not row:
        # Fuzzy match
        row = conn.execute("SELECT * FROM jobs WHERE url LIKE ? OR application_url LIKE ?",
                          (f"%{url.split('/')[-1]}%", f"%{url.split('/')[-1]}%")).fetchone()
    if not row:
        return None

    job = dict(row)
    job_url = job["url"]
    timeline = []

    # 2. Build timeline from job columns
    if job.get("discovered_at"):
        timeline.append({
            "timestamp": job["discovered_at"],
            "stage": "discover",
            "event": "discovered",
            "detail": f"source={job.get('strategy', '?')} site={job.get('site', '?')}",
        })

    if job.get("detail_scraped_at"):
        timeline.append({
            "timestamp": job["detail_scraped_at"],
            "stage": "enrich",
            "event": "enriched",
            "detail": f"description={len(job.get('full_description') or '')} chars",
        })

    if job.get("detail_error"):
        timeline.append({
            "timestamp": job.get("detail_scraped_at") or job.get("discovered_at", ""),
            "stage": "enrich",
            "event": "enrich_error",
            "detail": f"error={job['detail_error'][:100]} retries={job.get('detail_retry_count', 0)}",
        })

    if job.get("scored_at"):
        score_detail = f"score={job.get('fit_score')}"
        reasoning = job.get("score_reasoning", "")
        if reasoning:
            try:
                r = json.loads(reasoning)
                score_detail += f" confidence={r.get('confidence', '?')}"
                matched = r.get("matched_skills", [])
                missing = r.get("missing_requirements", [])
                if matched:
                    score_detail += f" matched={len(matched)}"
                if missing:
                    score_detail += f" missing={len(missing)}"
            except (json.JSONDecodeError, TypeError):
                score_detail += f" reasoning={reasoning[:80]}"
        timeline.append({
            "timestamp": job["scored_at"],
            "stage": "score",
            "event": "scored",
            "detail": score_detail,
        })

    if job.get("score_error"):
        timeline.append({
            "timestamp": job.get("scored_at") or "",
            "stage": "score",
            "event": "score_error",
            "detail": f"error={job['score_error'][:100]}",
        })

    if job.get("excluded_at"):
        timeline.append({
            "timestamp": job["excluded_at"],
            "stage": "score",
            "event": "excluded",
            "detail": f"reason={job.get('exclusion_reason_code', '?')} rule={job.get('exclusion_rule_id', '?')}",
        })

    if job.get("tailored_at"):
        timeline.append({
            "timestamp": job["tailored_at"],
            "stage": "tailor",
            "event": "tailored",
            "detail": f"attempts={job.get('tailor_attempts', 1)} pipeline={job.get('tailoring_pipeline', '?')} overlays={job.get('overlay_count', 0)}",
        })

    if job.get("cover_letter_at"):
        timeline.append({
            "timestamp": job["cover_letter_at"],
            "stage": "cover",
            "event": "cover_letter_generated",
            "detail": f"attempts={job.get('cover_attempts', 1)}",
        })

    if job.get("classified_at"):
        timeline.append({
            "timestamp": job["classified_at"],
            "stage": "apply",
            "event": "classified",
            "detail": f"tier={job.get('apply_tier', '?')} category={job.get('apply_category', '?')}",
        })

    if job.get("last_attempted_at"):
        timeline.append({
            "timestamp": job["last_attempted_at"],
            "stage": "apply",
            "event": "apply_attempted",
            "detail": f"status={job.get('apply_status', '?')} agent={job.get('agent_id', '?')} duration={job.get('apply_duration_ms', 0)}ms attempts={job.get('apply_attempts', 0)}",
        })

    if job.get("apply_error"):
        timeline.append({
            "timestamp": job.get("last_attempted_at") or job.get("applied_at", ""),
            "stage": "apply",
            "event": "apply_error",
            "detail": f"error={job['apply_error'][:200]}",
        })

    if job.get("applied_at") and job.get("apply_status") == "applied":
        timeline.append({
            "timestamp": job["applied_at"],
            "stage": "apply",
            "event": "applied",
            "detail": f"duration={job.get('apply_duration_ms', 0)}ms",
        })

    if job.get("needs_human_reason"):
        timeline.append({
            "timestamp": job.get("last_attempted_at") or "",
            "stage": "apply",
            "event": "needs_human",
            "detail": f"reason={job['needs_human_reason']} instructions={job.get('needs_human_instructions', '')[:80]}",
        })

    # 3. Analytics events for this job
    events = conn.execute(
        "SELECT timestamp, stage, event_type, payload FROM analytics_events WHERE payload LIKE ? ORDER BY timestamp",
        (f"%{job_url[:80]}%",)
    ).fetchall()
    for ev in events:
        payload = {}
        try:
            payload = json.loads(ev["payload"]) if ev["payload"] else {}
        except (json.JSONDecodeError, TypeError):
            pass
        # Skip if it's just a duplicate of what we already have
        timeline.append({
            "timestamp": ev["timestamp"],
            "stage": ev["stage"],
            "event": ev["event_type"],
            "detail": json.dumps(payload)[:200] if payload else "",
        })

    # 4. Sort chronologically
    timeline.sort(key=lambda x: x.get("timestamp") or "")

    # 5. Artifact files
    files = []
    for key in ("tailored_resume_path", "cover_letter_path", "tracking_doc_path"):
        path = job.get(key)
        if path:
            p = Path(path)
            files.append({
                "type": key.replace("_path", ""),
                "path": str(p),
                "exists": p.exists(),
                "size": p.stat().st_size if p.exists() else 0,
            })
            # Also check PDF variant
            pdf = p.with_suffix(".pdf")
            if pdf.exists():
                files.append({"type": key.replace("_path", "") + "_pdf", "path": str(pdf), "exists": True, "size": pdf.stat().st_size})

    # Check for agent log
    from applypilot import config
    log_dir = config.LOG_DIR
    if log_dir.exists():
        site = (job.get("site") or "").replace(" ", "_")
        for logf in sorted(log_dir.glob(f"agent_*{site}*"), reverse=True):
            files.append({"type": "agent_log", "path": str(logf), "exists": True, "size": logf.stat().st_size})
            break  # Most recent only

    # 6. Stats
    total_time = 0
    if job.get("discovered_at") and job.get("applied_at"):
        try:
            d = datetime.fromisoformat(job["discovered_at"])
            a = datetime.fromisoformat(job["applied_at"])
            total_time = (a - d).total_seconds()
        except (ValueError, TypeError):
            pass

    stats = {
        "total_stages": len(set(t["stage"] for t in timeline)),
        "total_events": len(timeline),
        "total_time_s": total_time,
        "score": job.get("fit_score"),
        "apply_status": job.get("apply_status"),
        "apply_attempts": job.get("apply_attempts", 0),
        "apply_duration_ms": job.get("apply_duration_ms", 0),
        "tailor_attempts": job.get("tailor_attempts", 0),
        "pipeline_status": job.get("pipeline_status"),
    }

    return {
        "job": {
            "url": job_url,
            "title": job.get("title"),
            "company": job.get("site"),
            "location": job.get("location"),
            "score": job.get("fit_score"),
            "status": job.get("apply_status") or job.get("pipeline_status") or "pending",
        },
        "timeline": timeline,
        "files": files,
        "stats": stats,
    }


def format_timeline(data: dict) -> str:
    """Format timeline data as human-readable text."""
    if not data:
        return "Job not found."

    lines = []
    job = data["job"]
    lines.append(f"{'=' * 70}")
    lines.append(f"  {job['title']} @ {job['company']}")
    lines.append(f"  Score: {job['score']}  Status: {job['status']}")
    lines.append(f"  URL: {job['url'][:80]}")
    lines.append(f"  Location: {job.get('location', '?')}")
    lines.append(f"{'=' * 70}")
    lines.append("")

    # Timeline
    _STAGE_ICONS = {
        "discover": "🔍", "enrich": "📄", "score": "⭐",
        "tailor": "✂️", "cover": "✉️", "apply": "🚀",
        "llm": "🤖", "analytics": "📊",
    }

    for entry in data["timeline"]:
        ts = entry.get("timestamp", "")[:19]
        icon = _STAGE_ICONS.get(entry["stage"], "•")
        event = entry["event"]
        detail = entry.get("detail", "")
        lines.append(f"  {ts}  {icon} [{entry['stage']:<8}] {event}")
        if detail:
            lines.append(f"                              {detail[:100]}")

    lines.append("")

    # Files
    if data["files"]:
        lines.append("  Artifacts:")
        for f in data["files"]:
            exists = "✅" if f["exists"] else "❌"
            size = f"{f['size'] // 1024}KB" if f["size"] else ""
            lines.append(f"    {exists} {f['type']:<25} {size:<8} {f['path']}")
        lines.append("")

    # Stats
    s = data["stats"]
    lines.append(f"  Stats: {s['total_events']} events across {s['total_stages']} stages")
    if s["apply_duration_ms"]:
        lines.append(f"  Apply: {s['apply_duration_ms']}ms, {s['apply_attempts']} attempts")
    if s["total_time_s"]:
        hours = s["total_time_s"] / 3600
        lines.append(f"  Total lifecycle: {hours:.1f} hours")

    return "\n".join(lines)
