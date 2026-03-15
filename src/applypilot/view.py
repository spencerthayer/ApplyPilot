"""ApplyPilot HTML Dashboard Generator.

Generates a self-contained HTML dashboard with:
  - Pipeline funnel visualization (clickable)
  - Active / Archive / Applied tabs
  - Stage filter buttons + score filters + text search
  - Colored stage badges on every job card
  - Inline tailored resume and cover letter previews
  - Apply agent log viewer
  - Pipeline timeline per job
"""

from __future__ import annotations

import glob as _glob
import os
import webbrowser
from datetime import datetime
from html import escape
from pathlib import Path

from rich.console import Console

from applypilot.config import APP_DIR, LOG_DIR
from applypilot.database import get_connection, get_stats

console = Console()

# Stage definitions: (label, bg_color, text_color)
STAGE_META = {
    "discovered":    ("Discovered",    "#64748b", "#e2e8f0"),
    "enrich_error":  ("Enrich Error",  "#ef4444", "#fecaca"),
    "enriched":      ("Enriched",      "#3b82f6", "#dbeafe"),
    "scored":        ("Scored <7",     "#f59e0b", "#fef3c7"),
    "scored_high":   ("Scored 7+",     "#10b981", "#d1fae5"),
    "tailor_failed": ("Tailor Failed", "#ef4444", "#fecaca"),
    "tailored":      ("Tailored",      "#14b8a6", "#ccfbf1"),
    "cover_ready":   ("Cover Ready",   "#06b6d4", "#cffafe"),
    "needs_human":   ("Needs Human",   "#7c3aed", "#ede9fe"),
    "applied":       ("Applied",       "#10b981", "#d1fae5"),
    # Apply category stages
    "blocked_auth":         ("Auth Barrier",      "#f59e0b", "#fef3c7"),
    "blocked_technical":    ("Technical Issue",   "#f97316", "#ffedd5"),
    "archived_ineligible":  ("Ineligible",        "#6b7280", "#e5e7eb"),
    "archived_expired":     ("Expired",           "#6b7280", "#e5e7eb"),
    "archived_platform":    ("Platform Blocked",  "#ef4444", "#fecaca"),
    "archived_no_url":      ("No URL",            "#6b7280", "#e5e7eb"),
    "manual_only":          ("Manual Only",       "#64748b", "#e2e8f0"),
    # Legacy stages (kept for backward compat until all rows have apply_category)
    "apply_failed":  ("Apply Failed",  "#ef4444", "#fecaca"),
    "apply_retry":   ("Retry Apply",   "#f97316", "#ffedd5"),
    # Tracking stages
    "track_confirmation": ("Confirmation", "#10b981", "#d1fae5"),
    "track_rejection":    ("Rejected",     "#ef4444", "#fecaca"),
    "track_interview":    ("Interview",    "#a855f7", "#f3e8ff"),
    "track_follow_up":    ("Follow-Up",    "#f59e0b", "#fef3c7"),
    "track_offer":        ("Offer",        "#14b8a6", "#ccfbf1"),
    "track_ghosted":      ("Ghosted",      "#64748b", "#e2e8f0"),
}

# Archived categories go to the archive tab
_ARCHIVED_CATEGORIES = {
    "archived_ineligible", "archived_expired", "archived_platform", "archived_no_url",
}

# Blocked categories stay in active (retryable)
_BLOCKED_CATEGORIES = {
    "blocked_auth", "blocked_technical",
}

# Apply error human-readable descriptions
_APPLY_ERROR_LABELS = {
    "expired": "Job posting expired",
    "captcha": "Blocked by CAPTCHA",
    "login_issue": "Login failed",
    "login_required": "Login required (retryable)",
    "not_eligible_location": "Location mismatch",
    "not_eligible_salary": "Salary below floor",
    "already_applied": "Already applied",
    "account_required": "Account creation required",
    "not_a_job_application": "Not a job application page",
    "unsafe_permissions": "Unsafe browser permissions requested",
    "unsafe_verification": "Video/biometric verification required",
    "sso_required": "SSO login required",
    "site_blocked": "Site blocked",
    "cloudflare_blocked": "Cloudflare blocked",
    "blocked_by_cloudflare": "Cloudflare blocked",
    "stuck": "Agent got stuck",
    "page_error": "Page error (500/blank)",
    "timeout": "Agent timed out",
    "no_result_line": "No result from agent",
}


def _classify_job(row) -> tuple[str, str]:
    """Classify a job into its pipeline stage and tab.

    Uses apply_category when available for precise classification of
    apply outcomes. Falls back to legacy error-based logic for rows
    that haven't been backfilled yet.

    Returns:
        (stage, tab) where tab is 'active', 'archive', 'applied', or 'tracking'.
    """
    # Check apply_category first (set by mark_result / backfill_categories)
    category = _safe_get(row, "apply_category")

    if row["apply_status"] == "needs_human":
        return "needs_human", "active"
    if row["applied_at"] and row["apply_status"] == "applied":
        # Jobs with tracking status move to the tracking tab
        try:
            tracking_status = row["tracking_status"]
        except (IndexError, KeyError):
            tracking_status = None
        if tracking_status:
            stage = f"track_{tracking_status}"
            if stage in STAGE_META:
                return stage, "tracking"
            return "applied", "tracking"
        return "applied", "applied"

    # Use category-based classification for apply outcomes
    if category:
        if category in _ARCHIVED_CATEGORIES:
            return category, "archive"
        if category in _BLOCKED_CATEGORIES:
            return category, "active"
        if category == "manual_only":
            return "manual_only", "archive"

    # Pre-apply pipeline stages (no category set)
    if row["cover_letter_path"] and not row["apply_error"]:
        return "cover_ready", "active"
    if row["tailored_resume_path"] and not row["apply_error"]:
        return "tailored", "active"
    if (row["tailor_attempts"] or 0) >= 5 and not row["tailored_resume_path"]:
        return "tailor_failed", "archive"
    if row["fit_score"] is not None:
        if row["fit_score"] >= 7:
            return "scored_high", "active"
        return "scored", "archive"
    if row["detail_error"]:
        return "enrich_error", "archive"
    if row["full_description"]:
        return "enriched", "active"
    return "discovered", "active"


def _read_file_safe(path: str | None, max_chars: int = 50000) -> str | None:
    """Read a text file, returning None if missing or unreadable."""
    if not path:
        return None
    try:
        p = Path(path)
        if p.exists() and p.suffix == ".txt":
            text = p.read_text(encoding="utf-8", errors="replace")
            return text[:max_chars]
    except OSError:
        pass
    return None


def _find_apply_log(site: str | None, last_attempted_at: str | None) -> str | None:
    """Find the apply agent log file by matching timestamp and site.

    Agent logs are named: claude_{YYYYMMDD_HHMMSS}_w{N}_{site}.txt
    Log filenames use local time; DB timestamps are UTC. We convert
    the DB timestamp to local time before comparing.
    """
    if not last_attempted_at or not site or not LOG_DIR.exists():
        return None

    try:
        attempt_dt = datetime.fromisoformat(last_attempted_at)
        # Convert UTC to local time (log filenames use local time)
        attempt_local = attempt_dt.astimezone().replace(tzinfo=None)
    except (ValueError, TypeError):
        return None

    site_slug = (site or "unknown")[:20]
    pattern = str(LOG_DIR / f"claude_*_{site_slug}.txt")
    candidates = _glob.glob(pattern)
    if not candidates:
        return None

    best_path = None
    best_delta = None
    for path in candidates:
        fname = os.path.basename(path)
        # claude_20260220_031252_w0_linkedin.txt
        parts = fname.replace("claude_", "").split("_")
        if len(parts) < 2:
            continue
        try:
            log_dt = datetime.strptime(f"{parts[0]}_{parts[1]}", "%Y%m%d_%H%M%S")
            delta = abs((attempt_local - log_dt).total_seconds())
            # Log must be within 30 minutes of the attempt
            if delta < 1800 and (best_delta is None or delta < best_delta):
                best_delta = delta
                best_path = path
        except (ValueError, IndexError):
            continue

    if best_path:
        return _read_file_safe(best_path, max_chars=100000)
    return None


def _fmt_ts(iso_str: str | None) -> str:
    """Format an ISO timestamp to a short readable string."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%b %d %H:%M")
    except (ValueError, TypeError):
        return ""


def _build_timeline(row) -> str:
    """Build a pipeline timeline HTML snippet from DB timestamps."""
    steps = []
    ts_fields = [
        ("discovered_at",     "Discovered",    "#64748b"),
        ("detail_scraped_at", "Enriched",      "#3b82f6"),
        ("scored_at",         "Scored",        "#f59e0b"),
        ("tailored_at",       "Tailored",      "#14b8a6"),
        ("cover_letter_at",   "Cover Letter",  "#06b6d4"),
        ("applied_at",        "Applied",       "#10b981"),
        ("last_attempted_at", "Last Attempt",  "#f97316"),
    ]

    for col, label, color in ts_fields:
        val = row[col]
        if val:
            ts_str = _fmt_ts(val)
            extra = ""
            if col == "scored_at" and row["fit_score"] is not None:
                extra = f" (score: {row['fit_score']})"
            if col == "last_attempted_at" and row["apply_error"]:
                err_label = _APPLY_ERROR_LABELS.get(row["apply_error"], row["apply_error"])
                extra = f" ({err_label})"
            if col == "applied_at" and row["apply_status"] == "applied":
                extra = " (success)"
            steps.append(
                f'<span class="tl-step" style="color:{color}">'
                f'<span class="tl-dot" style="background:{color}"></span>'
                f'{label}: {ts_str}{escape(extra)}'
                f'</span>'
            )

    # Add error entries if present
    if row["detail_error"]:
        steps.append(
            f'<span class="tl-step" style="color:#ef4444">'
            f'<span class="tl-dot" style="background:#ef4444"></span>'
            f'Enrich Error: {escape(row["detail_error"][:80])}'
            f'</span>'
        )

    if not steps:
        return ""
    return '<div class="timeline">' + "".join(steps) + "</div>"


def _build_artifacts_html(row) -> str:
    """Build expandable sections for tailored resume, cover letter, and apply log."""
    sections = []

    # Tailored resume
    resume_text = _read_file_safe(row["tailored_resume_path"])
    if resume_text:
        sections.append(
            "<details class='artifact-details'>"
            "<summary class='artifact-btn resume-btn'>Tailored Resume</summary>"
            f"<div class='artifact-content'>{escape(resume_text)}</div>"
            "</details>"
        )

    # Cover letter
    cl_text = _read_file_safe(row["cover_letter_path"])
    if cl_text:
        sections.append(
            "<details class='artifact-details'>"
            "<summary class='artifact-btn cover-btn'>Cover Letter</summary>"
            f"<div class='artifact-content'>{escape(cl_text)}</div>"
            "</details>"
        )

    # Apply log (agent narrative)
    if row["apply_attempts"] and row["apply_attempts"] > 0:
        agent_log = _find_apply_log(row["site"], row["last_attempted_at"])
        if agent_log:
            sections.append(
                "<details class='artifact-details'>"
                "<summary class='artifact-btn log-btn'>Apply Agent Log</summary>"
                f"<div class='artifact-content agent-log'>{escape(agent_log)}</div>"
                "</details>"
            )

    # Apply summary (error info, attempts, duration)
    apply_info = _build_apply_summary(row)
    if apply_info:
        sections.append(apply_info)

    if not sections:
        return ""
    return '<div class="artifacts">' + "".join(sections) + "</div>"


def _build_apply_summary(row) -> str:
    """Build a compact apply status summary."""
    parts = []
    if row["apply_status"]:
        status = row["apply_status"]
        if status == "applied":
            parts.append('<span class="apply-stat success">Applied</span>')
        elif status == "needs_human":
            reason = _safe_get(row, "needs_human_reason", "")
            parts.append(
                f'<span class="apply-stat" style="background:#4c1d9533;color:#c4b5fd">'
                f'Needs Human: {escape(reason)}</span>'
            )
        elif status == "failed":
            err = row["apply_error"] or "unknown"
            err_label = _APPLY_ERROR_LABELS.get(err, err)
            parts.append(f'<span class="apply-stat failed">{escape(err_label)}</span>')
        else:
            parts.append(f'<span class="apply-stat">{escape(status)}</span>')

    attempts = row["apply_attempts"] or 0
    if attempts > 0:
        parts.append(f'<span class="apply-detail">{attempts} attempt{"s" if attempts != 1 else ""}</span>')

    duration = row["apply_duration_ms"]
    if duration:
        secs = duration / 1000
        parts.append(f'<span class="apply-detail">{secs:.0f}s</span>')

    if not parts:
        return ""
    return '<div class="apply-summary">' + " ".join(parts) + "</div>"


def _safe_get(row, key, default=None):
    """Safely get a value from a sqlite3.Row or dict."""
    try:
        val = row[key]
        return val if val is not None else default
    except (IndexError, KeyError):
        return default


def _build_tracking_html(row) -> str:
    """Build tracking info HTML for a job card (status, next action, contacts)."""
    parts = []
    tracking_status = _safe_get(row, "tracking_status")
    if not tracking_status:
        return ""

    # Next action
    next_action = _safe_get(row, "next_action")
    if next_action:
        due = _safe_get(row, "next_action_due", "")
        due_html = f' <span class="tracking-due">(due: {escape(due)})</span>' if due else ""
        parts.append(
            f'<div class="tracking-action">'
            f'<span class="tracking-action-label">Next:</span> {escape(next_action)}{due_html}'
            f'</div>'
        )

    # Last email
    last_email = _safe_get(row, "last_email_at")
    if last_email:
        parts.append(
            f'<div class="tracking-detail">'
            f'Last email: {_fmt_ts(last_email)}'
            f'</div>'
        )

    # Link to tracking doc
    doc_path = _safe_get(row, "tracking_doc_path")
    if doc_path:
        parts.append(
            f'<div class="tracking-detail">'
            f'<a href="file:///{escape(doc_path)}" class="tracking-doc-link" target="_blank">View Tracking Doc</a>'
            f'</div>'
        )

    if not parts:
        return ""
    return '<div class="tracking-info">' + "".join(parts) + "</div>"


def generate_dashboard(output_path: str | None = None) -> str:
    """Generate an HTML dashboard of all jobs with pipeline awareness.

    Args:
        output_path: Where to write the HTML file. Defaults to ~/.applypilot/dashboard.html.

    Returns:
        Absolute path to the generated HTML file.
    """
    out = Path(output_path) if output_path else APP_DIR / "dashboard.html"
    conn = get_connection()

    stats = get_stats(conn)

    # Fetch ALL jobs with all timestamp columns
    jobs = conn.execute("""
        SELECT url, title, salary, description, location, site, strategy,
               full_description, application_url, detail_error,
               fit_score, score_reasoning,
               tailored_resume_path, tailored_at, tailor_attempts,
               cover_letter_path, cover_letter_at,
               applied_at, apply_status, apply_error, apply_attempts,
               apply_duration_ms, agent_id, last_attempted_at,
               discovered_at, detail_scraped_at, scored_at,
               company, tracking_status, tracking_updated_at,
               tracking_doc_path, last_email_at, next_action, next_action_due,
               needs_human_reason, needs_human_url, needs_human_instructions,
               apply_category
        FROM jobs
        ORDER BY fit_score DESC NULLS LAST, discovered_at DESC
    """).fetchall()

    # Classify each job
    tab_counts = {"active": 0, "archive": 0, "applied": 0, "tracking": 0}
    stage_counts: dict[str, int] = {}
    classified: list[tuple] = []
    for row in jobs:
        stage, tab = _classify_job(row)
        classified.append((row, stage, tab))
        tab_counts[tab] = tab_counts.get(tab, 0) + 1
        stage_counts[stage] = stage_counts.get(stage, 0) + 1

    # Funnel
    funnel = [
        ("Discovered",   stats["total"],              "#64748b"),
        ("Enriched",     stats["with_description"],   "#3b82f6"),
        ("Scored",       stats["scored"],             "#f59e0b"),
        ("Tailored",     stats["tailored"],           "#14b8a6"),
        ("Cover Letter", stats["with_cover_letter"],  "#06b6d4"),
        ("Applied",      stats["applied"],            "#10b981"),
    ]
    funnel_max = max(f[1] for f in funnel) if funnel else 1

    score_dist: dict[int, int] = {}
    for score, count in stats.get("score_distribution", []):
        score_dist[score] = count
    scored_total = stats["scored"]
    high_fit = sum(c for s, c in score_dist.items() if s >= 7)

    colors = {
        "RemoteOK": "#10b981", "WelcomeToTheJungle": "#f59e0b",
        "Hacker News Jobs": "#ff6600", "BuiltIn Remote": "#ec4899",
        "indeed": "#2164f3", "linkedin": "#0a66c2",
        "Dice": "#eb1c26", "Glassdoor": "#0caa41",
    }

    # Funnel HTML
    funnel_html = ""
    for label, count, color in funnel:
        pct = (count / funnel_max * 100) if funnel_max else 0
        funnel_html += f"""
        <div class="funnel-row" onclick="filterByFunnelStage('{label.lower().replace(' ', '_')}')" style="cursor:pointer">
          <span class="funnel-label">{label}</span>
          <div class="funnel-bar-track">
            <div class="funnel-bar-fill" style="width:{max(pct, 2)}%;background:{color}"></div>
          </div>
          <span class="funnel-count">{count:,}</span>
        </div>"""

    # Job cards
    job_cards = ""
    for row, stage, tab in classified:
        score = row["fit_score"]
        score_val = score if score is not None else 0
        title = escape(row["title"] or "Untitled")
        url = escape(row["url"] or "")
        salary = escape(row["salary"] or "")
        location = escape(row["location"] or "")
        site = escape(row["site"] or "")
        site_color = colors.get(row["site"] or "", "#6b7280")
        apply_url = escape(row["application_url"] or "")

        stage_label, stage_bg, stage_fg = STAGE_META.get(stage, ("?", "#6b7280", "#e2e8f0"))

        # Reasoning
        reasoning_raw = row["score_reasoning"] or ""
        reasoning_lines = reasoning_raw.split("\n")
        keywords = reasoning_lines[0][:120] if reasoning_lines else ""
        reasoning = reasoning_lines[1][:200] if len(reasoning_lines) > 1 else ""

        desc_preview = escape(row["full_description"] or "")[:300]
        full_desc_html = escape(row["full_description"] or "").replace("\n", "<br>")
        desc_len = len(row["full_description"] or "")

        # Meta tags
        meta_parts = [
            f'<span class="meta-tag site-tag" style="background:{site_color}33;color:{site_color}">{site}</span>'
        ]
        if salary:
            meta_parts.append(f'<span class="meta-tag salary">{salary}</span>')
        if location:
            meta_parts.append(f'<span class="meta-tag location">{location[:40]}</span>')
        meta_html = " ".join(meta_parts)

        # Score pill
        score_html = ""
        if score is not None:
            pill_bg = "#10b981" if score >= 7 else ("#f59e0b" if score >= 5 else "#ef4444")
            score_html = f'<span class="score-pill" style="background:{pill_bg}">{score}</span>'

        # Action links
        links = []
        if apply_url:
            links.append(f'<a href="{apply_url}" class="apply-link" target="_blank">Apply</a>')
        if url:
            links.append(f'<a href="{url}" class="view-link" target="_blank">View</a>')
        footer_html = " ".join(links)

        # Border color
        if stage == "needs_human":
            border_color = "#7c3aed"
        elif stage in ("applied", "scored_high", "cover_ready"):
            border_color = "#10b981"
        elif stage == "tailored":
            border_color = "#14b8a6"
        elif stage == "enriched":
            border_color = "#3b82f6"
        elif stage in ("enrich_error", "apply_failed", "tailor_failed", "archived_platform"):
            border_color = "#ef4444"
        elif stage in ("blocked_auth", "blocked_technical"):
            border_color = "#f59e0b"
        elif stage.startswith("archived_"):
            border_color = "#6b7280"
        elif stage == "scored":
            border_color = "#f59e0b"
        else:
            border_color = "#334155"

        # New features: timeline, artifacts, tracking
        timeline_html = _build_timeline(row)
        artifacts_html = _build_artifacts_html(row)
        tracking_html = _build_tracking_html(row)

        job_cards += f"""
        <div class="job-card" data-tab="{tab}" data-stage="{stage}" data-score="{score_val}" data-site="{escape(row['site'] or '')}" style="border-left-color:{border_color}">
          <div class="card-header">
            {score_html}
            <span class="stage-badge" style="background:{stage_bg};color:{stage_fg}">{stage_label}</span>
            <a href="{url}" class="job-title" target="_blank">{title}</a>
          </div>
          <div class="meta-row">{meta_html}</div>
          {f'<div class="keywords-row">{escape(keywords)}</div>' if keywords else ''}
          {f'<div class="reasoning-row">{escape(reasoning)}</div>' if reasoning else ''}
          {timeline_html}
          {tracking_html}
          {artifacts_html}
          {f'<p class="desc-preview">{desc_preview}...</p>' if desc_preview else ''}
          {_expand_desc(full_desc_html, desc_len) if row["full_description"] else ""}
          <div class="card-footer">{footer_html}</div>
        </div>"""

    # Stage filter buttons
    active_stages = ["needs_human", "blocked_auth", "blocked_technical", "enriched", "scored_high", "tailored", "cover_ready"]
    stage_btns_html = '<button class="filter-btn active" onclick="filterStage(\'all\')">All</button>\n'
    for s in active_stages:
        cnt = stage_counts.get(s, 0)
        if cnt > 0:
            label = STAGE_META[s][0]
            stage_btns_html += f'          <button class="filter-btn" onclick="filterStage(\'{s}\')">{label} ({cnt})</button>\n'

    html = _build_html(
        stats=stats,
        scored_total=scored_total,
        high_fit=high_fit,
        tab_counts=tab_counts,
        funnel_html=funnel_html,
        stage_btns_html=stage_btns_html,
        job_cards=job_cards,
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    abs_path = str(out.resolve())
    console.print(f"[green]Dashboard written to {abs_path}[/green]")
    return abs_path


def _expand_desc(full_desc_html: str, desc_len: int) -> str:
    return (
        "<details class='full-desc-details'>"
        f"<summary class='expand-btn'>Full Description ({desc_len:,} chars)</summary>"
        f"<div class='full-desc'>{full_desc_html}</div>"
        "</details>"
    )


def _build_html(
    stats: dict,
    scored_total: int,
    high_fit: int,
    tab_counts: dict,
    funnel_html: str,
    stage_btns_html: str,
    job_cards: str,
) -> str:
    total = stats["total"]
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ApplyPilot Dashboard</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background: #0f172a; color: #e2e8f0; padding: 2rem; }}

  h1 {{ font-size: 1.8rem; font-weight: 700; margin-bottom: 0.5rem; }}
  .subtitle {{ color: #94a3b8; margin-bottom: 2rem; }}

  /* Summary cards */
  .summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; margin-bottom: 2rem; }}
  .stat-card {{ background: #1e293b; border-radius: 12px; padding: 1.25rem; }}
  .stat-num {{ font-size: 2rem; font-weight: 700; }}
  .stat-label {{ color: #94a3b8; font-size: 0.85rem; margin-top: 0.25rem; }}

  /* Pipeline funnel */
  .funnel {{ background: #1e293b; border-radius: 12px; padding: 1.5rem; margin-bottom: 2rem; }}
  .funnel h3 {{ font-size: 1rem; margin-bottom: 1rem; color: #94a3b8; }}
  .funnel-row {{ display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem; padding: 0.3rem 0.5rem; border-radius: 6px; transition: background 0.15s; }}
  .funnel-row:hover {{ background: #334155; }}
  .funnel-label {{ width: 7rem; font-size: 0.85rem; font-weight: 600; flex-shrink: 0; }}
  .funnel-bar-track {{ flex: 1; height: 18px; background: #334155; border-radius: 4px; overflow: hidden; }}
  .funnel-bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.3s; min-width: 2px; }}
  .funnel-count {{ width: 4rem; font-size: 0.85rem; color: #94a3b8; text-align: right; font-weight: 600; }}

  /* Tabs */
  .tabs {{ display: flex; gap: 0; margin-bottom: 1.5rem; }}
  .tab-btn {{ background: #1e293b; border: none; color: #94a3b8; padding: 0.75rem 1.5rem; cursor: pointer; font-size: 0.9rem; font-weight: 600; transition: all 0.15s; border-bottom: 3px solid transparent; }}
  .tab-btn:first-child {{ border-radius: 8px 0 0 0; }}
  .tab-btn:last-child {{ border-radius: 0 8px 0 0; }}
  .tab-btn:hover {{ color: #e2e8f0; background: #334155; }}
  .tab-btn.active {{ color: #60a5fa; border-bottom-color: #60a5fa; background: #1e293b; }}
  .tab-count {{ font-size: 0.75rem; background: #334155; padding: 0.1rem 0.4rem; border-radius: 4px; margin-left: 0.4rem; }}

  /* Filters */
  .filters {{ background: #1e293b; border-radius: 12px; padding: 1.25rem; margin-bottom: 1.5rem; display: flex; gap: 0.75rem; flex-wrap: wrap; align-items: center; }}
  .filter-label {{ color: #94a3b8; font-size: 0.85rem; font-weight: 600; }}
  .filter-btn {{ background: #334155; border: none; color: #94a3b8; padding: 0.4rem 0.8rem; border-radius: 6px; cursor: pointer; font-size: 0.8rem; transition: all 0.15s; }}
  .filter-btn:hover {{ background: #475569; color: #e2e8f0; }}
  .filter-btn.active {{ background: #60a5fa; color: #0f172a; font-weight: 600; }}
  .search-input {{ background: #334155; border: 1px solid #475569; color: #e2e8f0; padding: 0.4rem 0.8rem; border-radius: 6px; font-size: 0.8rem; width: 220px; }}
  .search-input::placeholder {{ color: #64748b; }}
  .filter-sep {{ width: 1px; height: 1.5rem; background: #475569; }}

  /* Job grid */
  .job-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); gap: 1rem; }}
  .job-count {{ color: #94a3b8; font-size: 0.85rem; margin-bottom: 1rem; }}

  .job-card {{ background: #1e293b; border-radius: 10px; padding: 1rem; border-left: 3px solid #334155; transition: all 0.15s; }}
  .job-card:hover {{ transform: translateY(-2px); box-shadow: 0 4px 12px #00000044; }}

  .card-header {{ display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem; flex-wrap: wrap; }}
  .score-pill {{ display: inline-flex; align-items: center; justify-content: center; min-width: 1.6rem; height: 1.6rem; border-radius: 6px; color: #0f172a; font-weight: 700; font-size: 0.8rem; flex-shrink: 0; }}
  .stage-badge {{ font-size: 0.65rem; padding: 0.15rem 0.5rem; border-radius: 4px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; flex-shrink: 0; }}

  .job-title {{ color: #e2e8f0; text-decoration: none; font-weight: 600; font-size: 0.95rem; }}
  .job-title:hover {{ color: #60a5fa; }}

  .meta-row {{ display: flex; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 0.4rem; }}
  .meta-tag {{ font-size: 0.72rem; padding: 0.15rem 0.5rem; border-radius: 4px; background: #334155; color: #94a3b8; }}
  .meta-tag.salary {{ background: #064e3b; color: #6ee7b7; }}
  .meta-tag.location {{ background: #1e3a5f; color: #93c5fd; }}

  .keywords-row {{ font-size: 0.75rem; color: #10b981; margin-bottom: 0.3rem; line-height: 1.4; }}
  .reasoning-row {{ font-size: 0.75rem; color: #94a3b8; margin-bottom: 0.5rem; font-style: italic; line-height: 1.4; }}
  .desc-preview {{ font-size: 0.8rem; color: #64748b; line-height: 1.5; margin-bottom: 0.75rem; max-height: 3.6em; overflow: hidden; }}

  .card-footer {{ display: flex; justify-content: flex-end; gap: 0.5rem; }}
  .apply-link, .view-link {{ font-size: 0.8rem; text-decoration: none; padding: 0.3rem 0.8rem; border-radius: 6px; font-weight: 500; }}
  .apply-link {{ color: #10b981; border: 1px solid #10b98133; }}
  .apply-link:hover {{ background: #10b98122; }}
  .view-link {{ color: #60a5fa; border: 1px solid #60a5fa33; }}
  .view-link:hover {{ background: #60a5fa22; }}

  /* Expandable descriptions */
  .full-desc-details {{ margin-bottom: 0.75rem; }}
  .expand-btn {{ font-size: 0.8rem; color: #60a5fa; cursor: pointer; list-style: none; padding: 0.3rem 0; }}
  .expand-btn::-webkit-details-marker {{ display: none; }}
  .expand-btn:hover {{ color: #93c5fd; }}
  .full-desc {{ font-size: 0.8rem; color: #cbd5e1; line-height: 1.6; margin-top: 0.5rem; padding: 0.75rem; background: #0f172a; border-radius: 8px; max-height: 400px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; }}

  /* Pipeline timeline */
  .timeline {{ display: flex; flex-wrap: wrap; gap: 0.25rem 0.75rem; margin-bottom: 0.5rem; padding: 0.5rem 0.6rem; background: #0f172a; border-radius: 6px; }}
  .tl-step {{ font-size: 0.7rem; display: inline-flex; align-items: center; gap: 0.3rem; white-space: nowrap; }}
  .tl-dot {{ width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }}

  /* Artifact expandables (resume, cover letter, apply log) */
  .artifacts {{ display: flex; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 0.5rem; }}
  .artifact-details {{ flex: 1 1 100%; }}
  .artifact-btn {{ font-size: 0.75rem; cursor: pointer; list-style: none; padding: 0.3rem 0.7rem; border-radius: 5px; font-weight: 600; display: inline-block; }}
  .artifact-btn::-webkit-details-marker {{ display: none; }}
  .resume-btn {{ color: #14b8a6; background: #14b8a622; }}
  .resume-btn:hover {{ background: #14b8a644; }}
  .cover-btn {{ color: #06b6d4; background: #06b6d422; }}
  .cover-btn:hover {{ background: #06b6d444; }}
  .log-btn {{ color: #f97316; background: #f9731622; }}
  .log-btn:hover {{ background: #f9731644; }}
  .artifact-content {{ font-size: 0.78rem; color: #cbd5e1; line-height: 1.6; margin-top: 0.5rem; padding: 0.75rem; background: #0f172a; border-radius: 8px; max-height: 500px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; }}
  .artifact-content.agent-log {{ font-family: 'SF Mono', 'Menlo', 'Monaco', 'Consolas', monospace; font-size: 0.72rem; color: #94a3b8; }}

  /* Apply summary */
  .apply-summary {{ display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: center; margin-bottom: 0.5rem; }}
  .apply-stat {{ font-size: 0.72rem; padding: 0.15rem 0.5rem; border-radius: 4px; font-weight: 600; }}
  .apply-stat.success {{ background: #064e3b; color: #6ee7b7; }}
  .apply-stat.failed {{ background: #7f1d1d; color: #fca5a5; }}
  .apply-detail {{ font-size: 0.7rem; color: #64748b; }}

  /* Tracking info */
  .tracking-info {{ padding: 0.5rem 0.6rem; background: #0f172a; border-radius: 6px; margin-bottom: 0.5rem; border-left: 2px solid #a855f7; }}
  .tracking-action {{ font-size: 0.78rem; color: #e2e8f0; margin-bottom: 0.3rem; }}
  .tracking-action-label {{ font-weight: 600; color: #a855f7; }}
  .tracking-due {{ color: #f59e0b; font-size: 0.72rem; }}
  .tracking-detail {{ font-size: 0.72rem; color: #94a3b8; margin-top: 0.2rem; }}
  .tracking-doc-link {{ color: #60a5fa; text-decoration: none; }}
  .tracking-doc-link:hover {{ text-decoration: underline; }}

  .hidden {{ display: none !important; }}

  @media (max-width: 768px) {{
    .summary {{ grid-template-columns: repeat(2, 1fr); }}
    .job-grid {{ grid-template-columns: 1fr; }}
    body {{ padding: 1rem; }}
    .tabs {{ flex-wrap: wrap; }}
  }}
</style>
</head>
<body>

<h1>ApplyPilot Dashboard</h1>
<p class="subtitle">{total:,} jobs &middot; {scored_total} scored &middot; {high_fit} strong matches (7+)</p>

<div class="summary">
  <div class="stat-card"><div class="stat-num" style="color:#e2e8f0">{total:,}</div><div class="stat-label">Total Jobs</div></div>
  <div class="stat-card"><div class="stat-num" style="color:#3b82f6">{stats['with_description']:,}</div><div class="stat-label">Enriched</div></div>
  <div class="stat-card"><div class="stat-num" style="color:#f59e0b">{scored_total:,}</div><div class="stat-label">Scored</div></div>
  <div class="stat-card"><div class="stat-num" style="color:#10b981">{high_fit:,}</div><div class="stat-label">Strong Fit (7+)</div></div>
</div>

<div class="funnel">
  <h3>Pipeline Funnel</h3>
  {funnel_html}
</div>

<div class="tabs">
  <button class="tab-btn active" onclick="switchTab('active')">Active Pipeline<span class="tab-count">{tab_counts['active']}</span></button>
  <button class="tab-btn" onclick="switchTab('archive')">Archive<span class="tab-count">{tab_counts['archive']}</span></button>
  <button class="tab-btn" onclick="switchTab('applied')">Applied<span class="tab-count">{tab_counts['applied']}</span></button>
  <button class="tab-btn" onclick="switchTab('tracking')">Tracking<span class="tab-count">{tab_counts['tracking']}</span></button>
</div>

<div class="filters">
  <span class="filter-label">Stage:</span>
  {stage_btns_html}
  <div class="filter-sep"></div>
  <span class="filter-label">Score:</span>
  <button class="filter-btn score-btn active" onclick="filterScore(0)">All</button>
  <button class="filter-btn score-btn" onclick="filterScore(7)">7+</button>
  <button class="filter-btn score-btn" onclick="filterScore(8)">8+</button>
  <button class="filter-btn score-btn" onclick="filterScore(9)">9+</button>
  <div class="filter-sep"></div>
  <span class="filter-label">Search:</span>
  <input type="text" class="search-input" placeholder="Filter by title, site, location..." oninput="filterText(this.value)">
</div>

<div id="job-count" class="job-count"></div>
<div class="job-grid" id="job-grid">
{job_cards}
</div>

<script>
let currentTab = 'active';
let currentStage = 'all';
let minScore = 0;
let searchText = '';

function switchTab(tab) {{
  currentTab = tab;
  currentStage = 'all';
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  event.target.closest('.tab-btn').classList.add('active');
  document.querySelectorAll('.filters .filter-btn:not(.score-btn)').forEach((b, i) => {{
    b.classList.toggle('active', i === 0);
  }});
  applyFilters();
}}

function filterStage(stage) {{
  currentStage = stage;
  document.querySelectorAll('.filters .filter-btn:not(.score-btn)').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  applyFilters();
}}

function filterScore(min) {{
  minScore = min;
  document.querySelectorAll('.score-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  applyFilters();
}}

function filterText(text) {{
  searchText = text.toLowerCase();
  applyFilters();
}}

function filterByFunnelStage(funnelLabel) {{
  const mapping = {{
    'discovered': {{ tab: 'active', stage: 'discovered' }},
    'enriched': {{ tab: 'active', stage: 'enriched' }},
    'scored': {{ tab: 'active', stage: 'scored_high' }},
    'tailored': {{ tab: 'active', stage: 'tailored' }},
    'cover_letter': {{ tab: 'active', stage: 'cover_ready' }},
    'applied': {{ tab: 'applied', stage: 'all' }},
  }};
  const m = mapping[funnelLabel];
  if (m) {{
    currentTab = m.tab;
    currentStage = m.stage;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(b => {{
      if (b.textContent.toLowerCase().includes(currentTab === 'applied' ? 'applied' : (currentTab === 'archive' ? 'archive' : 'active'))) {{
        b.classList.add('active');
      }}
    }});
    applyFilters();
  }}
}}

function applyFilters() {{
  let shown = 0;
  let total = 0;
  document.querySelectorAll('.job-card').forEach(card => {{
    total++;
    const tab = card.dataset.tab;
    const stage = card.dataset.stage;
    const score = parseInt(card.dataset.score) || 0;
    const text = card.textContent.toLowerCase();

    const tabMatch = tab === currentTab;
    const stageMatch = currentStage === 'all' || stage === currentStage;
    const scoreMatch = minScore === 0 || score >= minScore;
    const textMatch = !searchText || text.includes(searchText);

    if (tabMatch && stageMatch && scoreMatch && textMatch) {{
      card.classList.remove('hidden');
      shown++;
    }} else {{
      card.classList.add('hidden');
    }}
  }});
  document.getElementById('job-count').textContent = 'Showing ' + shown.toLocaleString() + ' of ' + total.toLocaleString() + ' jobs';
}}

applyFilters();
</script>

</body>
</html>"""


def open_dashboard(output_path: str | None = None) -> None:
    """Generate the dashboard and open it in the default browser."""
    path = generate_dashboard(output_path)
    console.print("[dim]Opening in browser...[/dim]")
    webbrowser.open(f"file:///{path}")
