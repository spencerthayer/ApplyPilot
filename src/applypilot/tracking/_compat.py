"""Tracking compat — delegates to repos via DI container.

Functions accept optional `conn` parameter for backward compatibility
but ignore it — all DB access routes through the tracking repository.
"""

import dataclasses
from datetime import datetime, timezone

from applypilot.db.dto import TrackingEmailDTO, TrackingPersonDTO
from applypilot.url_safety import extract_company  # noqa: F401


def _tracking_repo():
    from applypilot.bootstrap import get_app

    return get_app().container.tracking_repo


def _job_repo():
    from applypilot.bootstrap import get_app

    return get_app().container.job_repo


def get_connection():
    """Route through DI container instead of direct SQLite import."""
    from applypilot.bootstrap import get_app

    return get_app().container._conn


def get_applied_jobs(conn=None) -> list[dict]:
    return [dataclasses.asdict(j) for j in _tracking_repo().get_applied_jobs()]


def update_tracking_status(job_url: str, new_status: str, conn=None) -> bool:
    return _tracking_repo().update_tracking_status(job_url, new_status)


def get_tracking_emails(job_url: str) -> list[dict]:
    return [dataclasses.asdict(e) for e in _tracking_repo().get_emails(job_url)]


def get_tracking_people(job_url: str) -> list[dict]:
    return [dataclasses.asdict(p) for p in _tracking_repo().get_people(job_url)]


def get_action_items() -> list[dict]:
    return [dataclasses.asdict(j) for j in _tracking_repo().get_action_items()]


def get_tracking_stats() -> dict:
    return _tracking_repo().get_stats()


def store_tracking_email(email: dict, conn=None) -> None:
    fields = {f.name for f in dataclasses.fields(TrackingEmailDTO)}
    _tracking_repo().store_email(TrackingEmailDTO(**{k: v for k, v in email.items() if k in fields}))


def store_tracking_person(person: dict, conn=None) -> None:
    fields = {f.name for f in dataclasses.fields(TrackingPersonDTO)}
    _tracking_repo().store_person(TrackingPersonDTO(**{k: v for k, v in person.items() if k in fields}))


def email_already_tracked(email_id: str, conn=None) -> bool:
    return _tracking_repo().email_exists(email_id)


def update_job_tracking_fields(job_url: str, fields: dict, conn=None) -> None:
    _tracking_repo().update_job_fields(job_url, fields)


def create_stub_job(email: dict, classification: str, conn=None) -> str:
    """Create a stub job entry for an email that doesn't match any applied job."""
    from applypilot.db.dto import JobDTO

    company = extract_company(email.get("sender", ""))
    subject = email.get("subject", "Unknown")
    stub_url = f"manual://{company or 'unknown'}/{subject[:50]}"

    now = datetime.now(timezone.utc).isoformat()
    job = JobDTO(
        url=stub_url,
        title=subject[:100],
        company=company,
        site="email",
        discovered_at=now,
        applied_at=now,
        apply_status="applied",
        tracking_status=classification,
        tracking_updated_at=now,
    )
    _job_repo().upsert(job)
    return stub_url
