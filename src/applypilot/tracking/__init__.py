"""ApplyPilot application response tracking.

Monitors Gmail for responses to job applications, classifies them with AI,
and maintains per-job tracking documents with timelines and action items.
"""

import logging
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table

from applypilot.database import (
    get_applied_jobs,
    get_connection,
    get_action_items,
    get_tracking_stats,
)

log = logging.getLogger(__name__)
console = Console()


def show_action_items() -> None:
    """Display pending action items as a Rich table."""
    items = get_action_items()
    if not items:
        console.print("[dim]No pending action items.[/dim]")
        return

    table = Table(title="Pending Action Items", show_header=True, header_style="bold cyan")
    table.add_column("Due", style="bold")
    table.add_column("Company")
    table.add_column("Title", max_width=25)
    table.add_column("Action", max_width=35)
    table.add_column("Status")

    for item in items:
        due = item["next_action_due"] or "N/A"
        company = item["company"] or "Unknown"
        title = (item["title"] or "Untitled")[:25]
        action = (item["next_action"] or "")[:35]
        status = item["tracking_status"] or ""
        table.add_row(due, company, title, action, status)

    console.print(table)


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
    from applypilot.database import (
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
        log.info("Matched email '%s' -> %s (score: %d)",
                 email.get("subject", "")[:50], job_url[:60], match["score"])
    else:
        if dry_run:
            console.print(
                f"  [dim]DRY RUN (new):[/dim] {email.get('subject', '')[:60]} "
                f"-> [bold]{classification}[/bold] (no matching job -- would create stub)"
            )
            return
        job_url = create_stub_job(email, classification, conn)
        counters["stubs"] += 1
        log.info("Created stub job for '%s' -> %s",
                 email.get("subject", "")[:50], job_url[:60])

    now = datetime.now(timezone.utc).isoformat()

    if dry_run:
        console.print(
            f"  [dim]DRY RUN:[/dim] {email.get('subject', '')[:60]} "
            f"-> [bold]{classification}[/bold] -> {job_url[:50]}"
        )
        return

    store_tracking_email({
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
        "extracted_data": json.dumps({
            "people": result.get("people", []),
            "dates": result.get("dates", []),
            "action_items": result.get("action_items", []),
            "summary": result.get("summary", ""),
        }),
        "classified_at": now,
    }, conn)

    update_tracking_status(job_url, classification, conn)
    update_job_tracking_fields(job_url, {
        "last_email_at": email.get("date", now),
    }, conn)

    action_items = result.get("action_items", [])
    if action_items:
        first = action_items[0]
        update_job_tracking_fields(job_url, {
            "next_action": first.get("task", ""),
            "next_action_due": first.get("deadline"),
        }, conn)

    for person in result.get("people", []):
        if person.get("email") or person.get("name"):
            store_tracking_person({
                "job_url": job_url,
                "name": person.get("name"),
                "title": person.get("title"),
                "email": person.get("email"),
                "source_email_id": email["id"],
                "first_seen_at": now,
            }, conn)


def remap_stubs(conn=None) -> dict:
    """Re-match emails under multi-company stubs to correct per-company stubs/jobs.

    Identifies stub jobs where multiple distinct companies' emails were collapsed
    together (e.g., 11 emails for 11 different companies all under one Greenhouse
    stub). For each such stub:
      1. Re-runs match_email_to_job() with the improved matcher against all applied jobs
      2. If a pipeline match is found: moves the email to that job
      3. If still unmatched: creates a proper per-company stub via create_stub_job()
      4. Deletes stub jobs that have zero emails remaining

    Returns:
        {remapped: int, new_stubs: int, deleted_stubs: int}
    """
    from applypilot.tracking.matcher import match_email_to_job, extract_company_from_subject
    from applypilot.database import (
        get_applied_jobs,
        create_stub_job,
        update_tracking_status,
    )

    if conn is None:
        conn = get_connection()

    applied_jobs = get_applied_jobs(conn)

    # Find all stub job_urls that have more than one email
    stub_rows = conn.execute("""
        SELECT job_url, COUNT(*) AS email_count
        FROM tracking_emails
        WHERE job_url LIKE 'manual://%'
        GROUP BY job_url
        HAVING COUNT(*) > 1
    """).fetchall()

    if not stub_rows:
        console.print("[dim]No multi-email stubs found to remap.[/dim]")
        return {"remapped": 0, "new_stubs": 0, "deleted_stubs": 0}

    console.print(f"  Found {len(stub_rows)} multi-email stubs to inspect")

    remapped = 0
    new_stubs = 0
    affected_job_urls: set[str] = set()

    for stub_row in stub_rows:
        stub_url = stub_row["job_url"]

        # Fetch all emails under this stub
        emails = conn.execute("""
            SELECT email_id, sender, sender_name, subject, snippet,
                   received_at, classification, body_text
            FROM tracking_emails WHERE job_url = ?
        """, (stub_url,)).fetchall()

        # Check how many distinct companies are mentioned across subjects
        companies = set()
        for e in emails:
            c = extract_company_from_subject(e["subject"] or "")
            if c:
                companies.add(c.lower())

        if len(companies) <= 1:
            # All emails plausibly about the same company — leave as-is
            continue

        console.print(f"  Remapping stub {stub_url[:60]} ({len(emails)} emails, "
                      f"{len(companies)} companies: {', '.join(sorted(companies)[:5])})")

        # Exclude the current stub so emails can't just re-match back to it
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

            # Try improved matcher (excluding the current stub)
            match = match_email_to_job(email_dict, jobs_without_stub)
            if match:
                new_url = match["job_url"]
                log.info("  remap: %s -> pipeline job %s (score=%d)",
                         email_row["email_id"], new_url[:50], match["score"])
            else:
                # Create a proper per-company stub
                new_url = create_stub_job(email_dict, classification, conn)
                if new_url != stub_url:
                    new_stubs += 1
                    log.info("  remap: %s -> new stub %s",
                             email_row["email_id"], new_url[:50])

            if new_url != stub_url:
                conn.execute(
                    "UPDATE tracking_emails SET job_url = ? WHERE email_id = ?",
                    (new_url, email_row["email_id"]),
                )
                affected_job_urls.add(new_url)
                remapped += 1

        conn.commit()

    # Re-compute tracking_status for all affected jobs
    for job_url in affected_job_urls:
        rows = conn.execute(
            "SELECT classification FROM tracking_emails WHERE job_url = ? "
            "ORDER BY received_at DESC",
            (job_url,),
        ).fetchall()
        for row in rows:
            if row["classification"]:
                update_tracking_status(job_url, row["classification"], conn)

    # Delete stub jobs that now have zero emails
    orphans = conn.execute("""
        SELECT url FROM jobs
        WHERE url LIKE 'manual://%'
          AND url NOT IN (SELECT DISTINCT job_url FROM tracking_emails)
    """).fetchall()
    deleted_stubs = 0
    for orphan in orphans:
        conn.execute("DELETE FROM jobs WHERE url = ?", (orphan["url"],))
        deleted_stubs += 1
    if deleted_stubs:
        conn.commit()

    console.print(f"  Remapped {remapped} emails to correct jobs/stubs")
    console.print(f"  Created {new_stubs} new per-company stubs")
    console.print(f"  Deleted {deleted_stubs} empty stub jobs")
    return {"remapped": remapped, "new_stubs": new_stubs, "deleted_stubs": deleted_stubs}


def relabel_all_tracked(conn=None) -> int:
    """Apply 'ap-track' Gmail label to all emails stored in tracking_emails.

    Used for one-time backfills and to catch emails tracked before the label
    feature existed. Safe to run repeatedly — Gmail ignores duplicate label adds.

    Returns:
        Count of emails submitted for labeling.
    """
    import asyncio
    from applypilot.tracking.gmail_client import apply_label_to_emails

    if conn is None:
        conn = get_connection()

    rows = conn.execute("SELECT email_id FROM tracking_emails").fetchall()
    email_ids = [r["email_id"] for r in rows]

    if not email_ids:
        console.print("[dim]No tracked emails found in DB.[/dim]")
        return 0

    console.print(f"  Applying 'ap-track' to {len(email_ids)} tracked emails...")
    count = asyncio.run(apply_label_to_emails(email_ids))
    console.print(f"  [green]Labeled {count} emails.[/green]")
    return count


def run_tracking(
    days: int = 14,
    ghosted_days: int = 7,
    limit: int = 100,
    dry_run: bool = False,
) -> dict:
    """Run the full tracking pipeline with triage optimization.

    Flow:
      1. Search Gmail for metadata only (no body reads)
      2. Deduplicate against tracking_emails table
      3. Triage with pure Python (auto-classify confirmations/rejections/noise)
      4. Read bodies only for LLM-needed emails
      5. LLM classify ambiguous/interview/offer emails
      6. Match all classified emails to jobs and store
      7. Apply 'ap-track' Gmail label to non-noise emails
      8. Detect ghosting
      9. Generate markdown docs
      10. Print summary with triage stats

    Returns:
        Dict with counts: {fetched, matched, classified, ghosted, errors, triage_savings_pct}
    """
    import asyncio

    from applypilot.tracking.gmail_client import search_application_emails, read_email_bodies
    from applypilot.tracking.classifier import classify_email
    from applypilot.tracking.triage import triage_batch
    from applypilot.tracking.ghosting import detect_ghosted
    from applypilot.tracking.markdown_gen import generate_tracking_doc
    from applypilot.database import (
        email_already_tracked,
        update_job_tracking_fields,
    )

    conn = get_connection()
    applied_jobs = get_applied_jobs(conn)

    console.print("\n[bold blue]Tracking Responses[/bold blue]")
    console.print(f"  Applied jobs: {len(applied_jobs)}")
    console.print(f"  Look-back:   {days} days")
    console.print(f"  Dry run:     {dry_run}\n")

    # 1. Search emails (metadata only — no body reads)
    try:
        emails = asyncio.run(search_application_emails(days=days, limit=limit))
    except Exception as e:
        console.print(f"[red]Gmail fetch failed:[/red] {e}")
        console.print("[dim]Run `applypilot track --setup` to verify Gmail connectivity.[/dim]")
        return {"fetched": 0, "matched": 0, "classified": 0, "ghosted": 0, "errors": 1}

    console.print(f"  Fetched {len(emails)} emails from Gmail")

    # 2. Deduplicate
    new_emails = [e for e in emails if not email_already_tracked(e["id"], conn)]
    console.print(f"  New emails:  {len(new_emails)} (skipped {len(emails) - len(new_emails)} duplicates)")

    if not new_emails:
        console.print("  Nothing new to process.")
        ghosted_count = 0
        if not dry_run:
            ghosted_count = detect_ghosted(applied_jobs, ghosted_days=ghosted_days, conn=conn)
        return {"fetched": len(emails), "matched": 0, "stubs": 0,
                "classified": 0, "ghosted": ghosted_count, "errors": 0,
                "triage_savings_pct": 0.0}

    # 3. Triage with pure Python
    triage_results, triage_stats = triage_batch(new_emails)
    console.print(f"  {triage_stats.summary()}")

    counters = {"matched": 0, "stubs": 0}
    classified_count = 0
    error_count = 0

    # 4. Process auto-classified emails (no body needed)
    for email, triage in triage_results:
        if triage.classification in ("confirmation", "rejection"):
            result = triage.to_classifier_dict()
            classified_count += 1
            _process_classified_email(email, result, applied_jobs, dry_run, conn, counters)
        elif triage.classification == "noise":
            pass  # Skip entirely

    # 5. Read bodies only for LLM-needed emails
    llm_emails = [email for email, triage in triage_results if triage.classification == "llm_needed"]

    if llm_emails:
        console.print(f"  Reading {len(llm_emails)} email bodies for LLM classification...")
        try:
            bodies = asyncio.run(read_email_bodies([e["id"] for e in llm_emails]))
        except Exception as e:
            log.warning("Body read failed: %s", e)
            bodies = {}

        # Merge bodies into the metadata emails
        for email in llm_emails:
            if email["id"] in bodies:
                full = bodies[email["id"]]
                email["body"] = full.get("body", "")
                email["thread_id"] = full.get("thread_id") or email.get("thread_id")

        # 6. LLM classify
        for email in llm_emails:
            try:
                result = classify_email(email)
                classified_count += 1
            except Exception as e:
                log.warning("Classification failed for email %s: %s", email["id"], e)
                result = {
                    "classification": "noise",
                    "confidence": 0.0,
                    "summary": "",
                    "people": [],
                    "dates": [],
                    "action_items": [],
                }
                error_count += 1

            _process_classified_email(email, result, applied_jobs, dry_run, conn, counters)

    # 7. Apply 'ap-track' Gmail label to non-noise emails
    if not dry_run:
        from applypilot.tracking.gmail_client import apply_label_to_emails
        labeled_ids = [
            email["id"] for email, triage in triage_results
            if triage.classification != "noise"
        ]
        if labeled_ids:
            labeled_count = asyncio.run(apply_label_to_emails(labeled_ids))
            if labeled_count:
                console.print(f"  Labeled {labeled_count} emails with 'ap-track'")

    # 8. Detect ghosting
    ghosted_count = 0
    if not dry_run:
        ghosted_count = detect_ghosted(applied_jobs, ghosted_days=ghosted_days, conn=conn)

    # 9. Generate markdown docs
    matched_count = counters["matched"]
    if not dry_run:
        doc_count = 0
        for job in applied_jobs:
            if job.get("tracking_status") or matched_count > 0:
                row = conn.execute("SELECT * FROM jobs WHERE url = ?", (job["url"],)).fetchone()
                if row and row["tracking_status"]:
                    path = generate_tracking_doc(dict(zip(row.keys(), row)), conn)
                    if path:
                        update_job_tracking_fields(job["url"], {"tracking_doc_path": path}, conn)
                        doc_count += 1
        if doc_count:
            console.print(f"  Generated {doc_count} tracking documents")

    # 10. Summary
    console.print("\n[bold]Tracking Summary[/bold]")
    console.print(f"  Emails fetched:   {len(emails)}")
    console.print(f"  New emails:       {len(new_emails)}")
    console.print(f"  Matched to jobs:  {matched_count}")
    if counters["stubs"]:
        console.print(f"  New jobs (manual): {counters['stubs']}")
    console.print(f"  Classified:       {classified_count}")
    console.print(f"  LLM calls:        {len(llm_emails)} (of {len(new_emails)} new)")
    console.print(f"  Triage savings:   {triage_stats.savings_pct:.0f}%")
    console.print(f"  Ghosted detected: {ghosted_count}")
    if error_count:
        console.print(f"  Errors:           {error_count}")

    # Show tracking stats
    tracking_stats = get_tracking_stats(conn)
    if tracking_stats:
        console.print("\n[bold]Status Breakdown[/bold]")
        for status, count in sorted(tracking_stats.items(), key=lambda x: -x[1]):
            emoji = {
                "confirmation": "[green]",
                "rejection": "[red]",
                "interview": "[magenta]",
                "follow_up": "[yellow]",
                "offer": "[cyan]",
                "ghosted": "[dim]",
            }.get(status, "[white]")
            console.print(f"  {emoji}{status}[/]: {count}")

    console.print()
    return {
        "fetched": len(emails),
        "matched": matched_count,
        "stubs": counters["stubs"],
        "classified": classified_count,
        "ghosted": ghosted_count,
        "errors": error_count,
        "triage_savings_pct": triage_stats.savings_pct,
    }
