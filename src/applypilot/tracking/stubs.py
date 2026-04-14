"""Stubs — extracted from tracking."""

from __future__ import annotations

import logging

from rich.console import Console

log = logging.getLogger(__name__)
console = Console()


def _tracking_repo():
    from applypilot.bootstrap import get_app

    return get_app().container.tracking_repo


def _job_repo():
    from applypilot.bootstrap import get_app

    return get_app().container.job_repo


def remap_stubs() -> dict:
    """Re-match emails under multi-company stubs to correct per-company stubs/jobs.

    Identifies stub jobs where multiple distinct companies' emails were collapsed
    together. For each such stub:
      1. Re-runs match_email_to_job() with the improved matcher against all applied jobs
      2. If a pipeline match is found: moves the email to that job
      3. If still unmatched: creates a proper per-company stub via create_stub_job()
      4. Deletes stub jobs that have zero emails remaining

    Returns:
        {remapped: int, new_stubs: int, deleted_stubs: int}
    """
    from applypilot.tracking.matcher import match_email_to_job, extract_company_from_subject
    from applypilot.tracking._compat import (
        get_applied_jobs,
        create_stub_job,
        update_tracking_status,
    )

    tracking = _tracking_repo()
    applied_jobs = get_applied_jobs()

    stub_rows = tracking.get_multi_email_stub_urls()

    if not stub_rows:
        console.print("[dim]No multi-email stubs found to remap.[/dim]")
        return {"remapped": 0, "new_stubs": 0, "deleted_stubs": 0}

    console.print(f"  Found {len(stub_rows)} multi-email stubs to inspect")

    remapped = 0
    new_stubs = 0
    affected_job_urls: set[str] = set()

    for stub_url, _count in stub_rows:
        emails = tracking.get_stub_email_dicts(stub_url)

        companies = set()
        for e in emails:
            c = extract_company_from_subject(e["subject"] or "")
            if c:
                companies.add(c.lower())

        if len(companies) <= 1:
            continue

        console.print(
            f"  Remapping stub {stub_url[:60]} ({len(emails)} emails, "
            f"{len(companies)} companies: {', '.join(sorted(companies)[:5])})"
        )

        jobs_without_stub = [j for j in applied_jobs if j["url"] != stub_url]

        for email_row in emails:
            email_dict = {
                "id": email_row["email_id"],
                "sender": email_row["sender"] or "",
                "sender_name": email_row["sender_name"] or "",
                "subject": email_row["subject"] or "",
                "snippet": email_row["snippet"] or "",
                "date": email_row["received_at"] or "",
                "body": email_row["body_text"] or "",
            }
            classification = email_row["classification"] or "confirmation"

            match = match_email_to_job(email_dict, jobs_without_stub)
            if match:
                new_url = match["job_url"]
                log.info(
                    "  remap: %s -> pipeline job %s (score=%d)", email_row["email_id"], new_url[:50], match["score"]
                )
            else:
                new_url = create_stub_job(email_dict, classification)
                if new_url != stub_url:
                    new_stubs += 1
                    log.info("  remap: %s -> new stub %s", email_row["email_id"], new_url[:50])

            if new_url != stub_url:
                tracking.move_email_to_job(email_row["email_id"], new_url)
                affected_job_urls.add(new_url)
                remapped += 1

    # Re-compute tracking_status for all affected jobs
    for job_url in affected_job_urls:
        emails = tracking.get_emails(job_url)
        for email in emails:
            if email.classification:
                update_tracking_status(job_url, email.classification)

    # Delete stub jobs that now have zero emails
    deleted_stubs = tracking.delete_orphan_stubs()

    console.print(f"  Remapped {remapped} emails to correct jobs/stubs")
    console.print(f"  Created {new_stubs} new per-company stubs")
    console.print(f"  Deleted {deleted_stubs} empty stub jobs")
    return {"remapped": remapped, "new_stubs": new_stubs, "deleted_stubs": deleted_stubs}


def relabel_all_tracked() -> int:
    """Apply 'ap-track' Gmail label to all emails stored in tracking_emails.

    Returns:
        Count of emails submitted for labeling.
    """
    import asyncio
    from applypilot.tracking.gmail_client import apply_label_to_emails

    email_ids = _tracking_repo().get_all_email_ids()

    if not email_ids:
        console.print("[dim]No tracked emails found in DB.[/dim]")
        return 0

    console.print(f"  Applying 'ap-track' to {len(email_ids)} tracked emails...")
    count = asyncio.run(apply_label_to_emails(email_ids))
    console.print(f"  [green]Labeled {count} emails.[/green]")
    return count
