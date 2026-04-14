"""Result tracking — update job apply status in the database."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from applypilot.db.dto import ApplyResultDTO

PERMANENT_FAILURES: set[str] = {
    "expired",
    "job_expired",
    "captcha",
    "login_issue",
    "login_required",
    "llm_error",
    "not_eligible_location",
    "not_eligible_salary",
    "already_applied",
    "account_required",
    "not_a_job_application",
    "unsafe_permissions",
    "unsafe_verification",
    "sso_required",
    "site_blocked",
    "cloudflare_blocked",
    "blocked_by_cloudflare",
    "no_form_found",
}

PERMANENT_PREFIXES: tuple[str, ...] = ("site_blocked", "cloudflare", "blocked_by")


def _is_permanent_failure(result: str) -> bool:
    """Determine if a failure should never be retried."""
    reason = result.split(":", 1)[-1] if ":" in result else result
    return (
            result in PERMANENT_FAILURES
            or reason in PERMANENT_FAILURES
            or any(reason.startswith(p) for p in PERMANENT_PREFIXES)
    )


def mark_result(
        url: str,
        status: str,
        error: str | None = None,
        permanent: bool = False,
        duration_ms: int | None = None,
        task_id: str | None = None,
) -> None:
    """Update a job's apply status in the database."""
    from applypilot.bootstrap import get_app

    repo = get_app().container.job_repo
    now = datetime.now(timezone.utc).isoformat()

    if status == "applied":
        repo.update_apply_status(
            ApplyResultDTO(
                url=url,
                apply_status="applied",
                applied_at=now,
                apply_duration_ms=duration_ms,
            )
        )
        # Feedback loop: record outcome for bullets used in this job
        try:
            from applypilot.scoring.tailor.hybrid_bridge import record_apply_feedback

            app = get_app()
            job = repo.find_by_url_fuzzy(url)
            title = job.title if job else ""
            record_apply_feedback(url, "applied", title, app.container.overlay_repo, app.container.piece_repo)
        except Exception:
            pass
        # LinkedIn outreach draft
        try:
            from applypilot.outreach.linkedin import draft_outreach_after_apply

            draft = draft_outreach_after_apply(url)
            if draft:
                from rich.console import Console

                Console().print(
                    f"\n[bold]💡 LinkedIn Outreach Draft:[/bold]\n"
                    f"  {draft['message']}\n"
                    f"  [dim]({draft['char_count']} chars — copy to LinkedIn connection request)[/dim]"
                )
        except Exception:
            pass
    else:
        repo.update_apply_status(
            ApplyResultDTO(
                url=url,
                apply_status=status,
                apply_error=error or "unknown",
                apply_duration_ms=duration_ms,
            )
        )
        if permanent:
            repo.mark_permanent_failure(url)
        else:
            repo.increment_attempts(url, "apply_attempts")


def release_lock(url: str) -> None:
    """Release the in_progress lock without changing status."""
    from applypilot.bootstrap import get_app

    get_app().container.job_repo.update_apply_status(
        ApplyResultDTO(
            url=url,
            apply_status=None,
        )
    )


def mark_job(url: str, status: str, reason: str | None = None) -> None:
    """Manually mark a job's apply status in the database."""
    from applypilot.bootstrap import get_app

    now = datetime.now(timezone.utc).isoformat()
    repo = get_app().container.job_repo
    if status == "applied":
        repo.update_apply_status(
            ApplyResultDTO(
                url=url,
                apply_status="applied",
                applied_at=now,
            )
        )
    else:
        repo.update_apply_status(
            ApplyResultDTO(
                url=url,
                apply_status="failed",
                apply_error=reason or "manual",
            )
        )


def reset_failed() -> int:
    """Reset all failed jobs so they can be retried."""
    from applypilot.bootstrap import get_app

    return get_app().container.job_repo.reset_failed_jobs()


def _fallback_failure_reason(output: str, returncode: int, agent: str) -> str:
    """Return a deterministic failure reason when no RESULT line was emitted."""
    if returncode:
        last_line = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), "")
        if last_line:
            cleaned = re.sub(r"[^a-zA-Z0-9._:-]+", "_", last_line.lower()).strip("_")
            return f"{agent}_runtime_error:{cleaned[:60] or returncode}"
        return f"{agent}_runtime_error:{returncode}"
    return "no_result_line"
