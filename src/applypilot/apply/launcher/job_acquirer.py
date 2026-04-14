"""Job acquisition — atomically claim the next job from the apply queue."""

from __future__ import annotations

import dataclasses
import logging

from applypilot import config

logger = logging.getLogger(__name__)


def _load_blocked():
    from applypilot.config import load_blocked_sites

    return load_blocked_sites()


def acquire_job(target_url: str | None = None, min_score: int = 7, worker_id: int = 0) -> dict | None:
    """Atomically acquire the next job to apply to.

    Returns:
        Job dict or None if the queue is empty.
    """
    from applypilot.bootstrap import get_app

    job_repo = get_app().container.job_repo
    agent_id = f"worker-{worker_id}"

    while True:
        if target_url:
            row = job_repo.get_target_job(target_url)
        else:
            blocked_sites, blocked_patterns = _load_blocked()
            row = job_repo.acquire_next_filtered(
                min_score=min_score,
                max_attempts=config.DEFAULTS["max_apply_attempts"],
                agent_id=agent_id,
                blocked_sites=blocked_sites,
                blocked_patterns=blocked_patterns,
            )

        if not row:
            return None

        from applypilot.config import is_manual_ats

        apply_url = row.application_url or row.url
        if is_manual_ats(apply_url) and not target_url:
            job_repo.park_for_human_review(
                url=row.url,
                reason="manual ATS (login/CAPTCHA required)",
                apply_url=apply_url,
                instructions="Log in or solve the CAPTCHA, then click Done so the agent can continue.",
            )
            logger.info("Parked for human review (manual ATS): %s", row.url[:80])
            continue

        if target_url:
            job_repo.lock_for_apply(row.url, agent_id)

        return dataclasses.asdict(row)


def _target_unavailable_reason(target_url: str, min_score: int) -> str:
    """Return a user-facing reason when a targeted URL cannot be acquired."""
    from applypilot.bootstrap import get_app

    job_repo = get_app().container.job_repo
    row = job_repo.find_by_url_fuzzy(target_url)

    if not row:
        return "target URL not found in database"

    if not row.tailored_resume_path:
        return "missing tailored resume for this job"
    score = row.fit_score
    if score is not None and score < min_score:
        return f"fit score {score} is below min-score {min_score}"
    status = (row.apply_status or "").lower().strip()
    match status:
        case "applied":
            return "already marked applied"
        case "in_progress":
            return "already in progress on another worker"
        case "manual":
            return "marked manual ATS"
        case s if s and s != "failed":
            return f"status is '{s}'"
    return "job is not currently eligible for auto-apply"
