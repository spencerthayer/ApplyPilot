"""LinkedIn outreach — draft connection messages after apply.

Triggered automatically after successful apply. Generates 300-char
messages for hiring managers, saved as drafts in tracking_people table.
Only for LinkedIn applications.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_MESSAGE_TEMPLATE = "{hook} — {proof}. Would love to chat about {topic} for 15 min."


def generate_outreach(job: dict, top_bullet: str) -> dict | None:
    """Generate a LinkedIn outreach draft for a job. Returns None if not LinkedIn."""
    url = job.get("url", "")
    if "linkedin.com" not in url:
        return None

    company = job.get("company") or _extract_company(job.get("title", ""))
    title = job.get("title", "")
    matched_skills = job.get("matched_skills", [])

    hook = f"Your {company} team's work on {matched_skills[0] if matched_skills else title.split()[0]} caught my eye"
    proof = top_bullet[:120] if top_bullet else "I've built production systems in this space"
    topic = matched_skills[0] if matched_skills else "the role"

    message = f"{hook} — {proof}. Would love to chat about {topic} for 15 min."

    # Trim to 300 chars (LinkedIn limit)
    if len(message) > 300:
        message = message[:297] + "..."

    return {
        "company": company,
        "role": title,
        "job_url": url,
        "message": message,
        "char_count": len(message),
    }


def draft_outreach_after_apply(job_url: str) -> dict | None:
    """Called after successful apply — drafts outreach if LinkedIn job."""
    try:
        from applypilot.bootstrap import get_app

        app = get_app()
        job = app.container.job_repo.find_by_url_fuzzy(job_url)
        if not job:
            return None

        import dataclasses

        job_dict = dataclasses.asdict(job) if hasattr(job, "__dataclass_fields__") else dict(job)

        if "linkedin.com" not in job_dict.get("url", ""):
            return None

        # Get best bullet from pieces
        top_bullet = ""
        try:
            bullets = app.container.piece_repo.get_by_type("bullet")
            if bullets:
                top_bullet = bullets[0].content
        except Exception:
            pass

        draft = generate_outreach(job_dict, top_bullet)
        if not draft:
            return None

        # Save draft to tracking_people
        try:
            from applypilot.db.connection import get_connection

            conn = get_connection()
            conn.execute(
                "INSERT OR IGNORE INTO tracking_people (id, company, role, linkedin_url, notes) VALUES (?, ?, ?, ?, ?)",
                (job_url[:50], draft["company"], draft["role"], job_url, draft["message"]),
            )
            conn.commit()
        except Exception as e:
            log.debug("Outreach draft save failed: %s", e)

        log.info("Outreach draft generated for %s (%d chars)", draft["company"], draft["char_count"])
        return draft
    except Exception as e:
        log.debug("Outreach generation failed: %s", e)
        return None


def _extract_company(title: str) -> str:
    """Extract company from title like 'SDE at Amazon'."""
    for sep in (" at ", " @ ", " - ", " | "):
        if sep in title:
            return title.split(sep)[-1].strip()
    return "the company"
