"""Email Processor — extracted from tracking."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from rich.console import Console

log = logging.getLogger(__name__)
console = Console()


def _process_classified_email(
        email: dict,
        result: dict,
        applied_jobs: list[dict],
        dry_run: bool,
        conn,
        counters: dict,
) -> None:
    """Match a classified email to a job and store results.

    Shared logic for both triage-classified and LLM-classified emails.
    Mutates counters dict in place.
    """
    import json

    from applypilot.tracking.matcher import match_email_to_job
    from applypilot.tracking._compat import (
        store_tracking_email,
        store_tracking_person,
        update_tracking_status,
        update_job_tracking_fields,
        create_stub_job,
    )

    classification = result["classification"]
    if classification == "noise":
        return

    match = match_email_to_job(email, applied_jobs)

    if match:
        job_url = match["job_url"]
        counters["matched"] += 1
        log.info("Matched email '%s' -> %s (score: %d)", email.get("subject", "")[:50], job_url[:60], match["score"])
    else:
        if dry_run:
            console.print(
                f"  [dim]DRY RUN (new):[/dim] {email.get('subject', '')[:60]} "
                f"-> [bold]{classification}[/bold] (no matching job -- would create stub)"
            )
            return
        job_url = create_stub_job(email, classification, conn)
        counters["stubs"] += 1
        log.info("Created stub job for '%s' -> %s", email.get("subject", "")[:50], job_url[:60])

    now = datetime.now(timezone.utc).isoformat()

    if dry_run:
        console.print(
            f"  [dim]DRY RUN:[/dim] {email.get('subject', '')[:60]} -> [bold]{classification}[/bold] -> {job_url[:50]}"
        )
        return

    store_tracking_email(
        {
            "email_id": email["id"],
            "thread_id": email.get("thread_id"),
            "job_url": job_url,
            "sender": email.get("sender"),
            "sender_name": email.get("sender_name"),
            "subject": email.get("subject"),
            "received_at": email.get("date"),
            "snippet": email.get("snippet"),
            "body_text": email.get("body", ""),
            "classification": classification,
            "extracted_data": json.dumps(
                {
                    "people": result.get("people", []),
                    "dates": result.get("dates", []),
                    "action_items": result.get("action_items", []),
                    "summary": result.get("summary", ""),
                }
            ),
            "classified_at": now,
        },
        conn,
    )

    update_tracking_status(job_url, classification, conn)
    update_job_tracking_fields(
        job_url,
        {
            "last_email_at": email.get("date", now),
        },
        conn,
    )

    action_items = result.get("action_items", [])
    if action_items:
        first = action_items[0]
        update_job_tracking_fields(
            job_url,
            {
                "next_action": first.get("task", ""),
                "next_action_due": first.get("deadline"),
            },
            conn,
        )

    for person in result.get("people", []):
        if person.get("email") or person.get("name"):
            store_tracking_person(
                {
                    "job_url": job_url,
                    "name": person.get("name"),
                    "title": person.get("title"),
                    "email": person.get("email"),
                    "source_email_id": email["id"],
                    "first_seen_at": now,
                },
                conn,
            )
