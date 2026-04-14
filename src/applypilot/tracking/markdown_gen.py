"""Generate per-job markdown tracking documents.

Each applied job with a tracking status gets a markdown file at:
  ~/.applypilot/tracking/{company}_{title}_{hash}.md

The ## Notes section is preserved across regenerations.
"""

import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from applypilot.config import TRACKING_DIR

log = logging.getLogger(__name__)

# Tracking status display labels with emoji
STATUS_DISPLAY = {
    "confirmation": "\U0001f4e8 Confirmation Received",
    "rejection": "\u274c Rejected",
    "interview": "\U0001f4c5 Interview Scheduled",
    "follow_up": "\U0001f4cb Follow-Up Requested",
    "offer": "\U0001f389 Offer Received",
    "ghosted": "\U0001f47b Ghosted",
}


def _slugify(text: str, max_len: int = 30) -> str:
    """Convert text to a filesystem-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:max_len]


def _make_filename(job: dict) -> str:
    """Generate a stable filename for a job's tracking doc."""
    company = _slugify(job.get("company") or "unknown", 20)
    title = _slugify(job.get("title") or "untitled", 30)
    url_hash = hashlib.md5(job["url"].encode()).hexdigest()[:8]
    return f"{company}_{title}_{url_hash}.md"


def _read_existing_notes(path: Path) -> str:
    """Extract the ## Notes section from an existing tracking doc."""
    if not path.exists():
        return ""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return ""

    # Find ## Notes section and extract until next ##
    match = re.search(r"## Notes\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


def _fmt_date(iso_str: str | None) -> str:
    """Format an ISO date to readable short form."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d")
    except (ValueError, TypeError):
        return iso_str[:10] if iso_str else ""


def generate_tracking_doc(job: dict, conn=None) -> str | None:
    """Generate (or update) a markdown tracking document for a job.

    Args:
        job: Full job dict (all columns from jobs table).
        conn: Database connection for fetching tracking data.

    Returns:
        Absolute path to the markdown file, or None on error.
    """
    from applypilot.tracking._compat import get_tracking_emails, get_tracking_people

    tracking_status = job.get("tracking_status")
    if not tracking_status:
        return None

    filename = _make_filename(job)
    path = TRACKING_DIR / filename

    # Preserve existing notes
    existing_notes = _read_existing_notes(path)

    # Gather data
    emails = get_tracking_emails(job["url"])
    people = get_tracking_people(job["url"])

    # Build document
    title = job.get("title") or "Untitled"
    company = job.get("company") or "Unknown"
    status_display = STATUS_DISPLAY.get(tracking_status, tracking_status)
    score = job.get("fit_score")
    score_str = f"{score}/10" if score is not None else "N/A"

    lines = [
        f"# {title} @ {company.title()}",
        "",
        f"**Status:** {status_display}",
        f"**Applied:** {_fmt_date(job.get('applied_at'))}  |  **Score:** {score_str}",
        f"**Job URL:** [{job['url'][:60]}...]({job['url']})"
        if len(job["url"]) > 60
        else f"**Job URL:** [{job['url']}]({job['url']})",
    ]

    if job.get("application_url"):
        lines.append(
            f"**Application:** [{job['application_url'][:60]}...]({job['application_url']})"
            if len(job["application_url"]) > 60
            else f"**Application:** [{job['application_url']}]({job['application_url']})"
        )

    lines.extend(["", "---", ""])

    # Timeline
    lines.append("## Timeline")
    lines.append("")
    lines.append("| Date | Event | Details |")
    lines.append("|------|-------|---------|")

    # Applied event
    lines.append(f"| {_fmt_date(job.get('applied_at'))} | Applied | Via {job.get('site') or 'unknown'} |")

    # Email events
    for email in emails:
        classification = email.get("classification", "")
        event_label = {
            "confirmation": "Confirmation",
            "rejection": "Rejection",
            "interview": "Interview",
            "follow_up": "Follow-Up",
            "offer": "Offer",
        }.get(classification, classification.title())

        # Get summary from extracted_data
        summary = ""
        if email.get("extracted_data"):
            try:
                data = json.loads(email["extracted_data"])
                summary = data.get("summary", "")
            except (json.JSONDecodeError, TypeError):
                pass
        if not summary:
            summary = (email.get("snippet") or email.get("subject") or "")[:60]

        lines.append(f"| {_fmt_date(email.get('received_at'))} | {event_label} | {summary} |")

    # Ghosted note
    if tracking_status == "ghosted" and not emails:
        lines.append(f"| {_fmt_date(job.get('tracking_updated_at'))} | Ghosted | No response received |")

    lines.extend(["", ""])

    # Key People
    if people:
        lines.append("## Key People")
        lines.append("")
        lines.append("| Name | Title | Email |")
        lines.append("|------|-------|-------|")
        for p in people:
            name = p.get("name") or ""
            ptitle = p.get("title") or ""
            pemail = p.get("email") or ""
            lines.append(f"| {name} | {ptitle} | {pemail} |")
        lines.extend(["", ""])

    # Action Items
    lines.append("## Action Items")
    lines.append("")

    # Gather action items from all emails
    has_actions = False
    for email in emails:
        if email.get("extracted_data"):
            try:
                data = json.loads(email["extracted_data"])
                for item in data.get("action_items", []):
                    task = item.get("task", "")
                    deadline = item.get("deadline")
                    dl_str = f" (due: {deadline})" if deadline else ""
                    lines.append(f"- [ ] {task}{dl_str}")
                    has_actions = True
            except (json.JSONDecodeError, TypeError):
                pass

    # Always show the "Submit application" as completed
    lines.append(f"- [x] Submit application ({_fmt_date(job.get('applied_at'))})")

    if not has_actions:
        pass  # Just the completed submit item

    lines.extend(["", ""])

    # Notes section (preserved)
    lines.append("## Notes")
    lines.append("")
    if existing_notes:
        lines.append(existing_notes)
    else:
        lines.append("<!-- Edit this section freely — preserved across tracking updates -->")
    lines.extend(["", ""])

    # Email Thread
    if emails:
        lines.append("## Email Thread")
        lines.append("")
        for email in reversed(emails):  # Most recent first
            date_str = _fmt_date(email.get("received_at"))
            subject = email.get("subject") or "(no subject)"
            sender_name = email.get("sender_name") or email.get("sender") or "unknown"
            sender_email = email.get("sender") or ""

            lines.append(f"### {date_str}: {subject}")
            lines.append(f"**From:** {sender_name} <{sender_email}>")

            body = email.get("body_text") or email.get("snippet") or ""
            if body:
                # Quote the body
                quoted = "\n".join(f"> {line}" for line in body[:2000].split("\n"))
                lines.append(quoted)
            lines.extend(["", ""])

    # Write file
    try:
        TRACKING_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        return str(path.resolve())
    except OSError as e:
        log.error("Failed to write tracking doc %s: %s", path, e)
        return None
