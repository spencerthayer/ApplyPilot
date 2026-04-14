"""Pipeline — extracted from tracking."""

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
    import dataclasses

    from applypilot.tracking.gmail_client import search_application_emails, read_email_bodies
    from applypilot.tracking.classifier import classify_email
    from applypilot.tracking.triage import triage_batch
    from applypilot.tracking.ghosting import detect_ghosted
    from applypilot.tracking.markdown_gen import generate_tracking_doc
    from applypilot.tracking._compat import (
        email_already_tracked,
        update_job_tracking_fields,
        get_applied_jobs,
        get_tracking_stats,
    )
    from applypilot.tracking.email_processor import _process_classified_email

    job_repo = _job_repo()
    applied_jobs = get_applied_jobs()

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
    new_emails = [e for e in emails if not email_already_tracked(e["id"])]
    console.print(f"  New emails:  {len(new_emails)} (skipped {len(emails) - len(new_emails)} duplicates)")

    if not new_emails:
        console.print("  Nothing new to process.")
        ghosted_count = 0
        if not dry_run:
            ghosted_count = detect_ghosted(applied_jobs, ghosted_days=ghosted_days)
        return {
            "fetched": len(emails),
            "matched": 0,
            "stubs": 0,
            "classified": 0,
            "ghosted": ghosted_count,
            "errors": 0,
            "triage_savings_pct": 0.0,
        }

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
            _process_classified_email(email, result, applied_jobs, dry_run, None, counters)
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

            _process_classified_email(email, result, applied_jobs, dry_run, None, counters)

    # 7. Apply 'ap-track' Gmail label to non-noise emails
    if not dry_run:
        from applypilot.tracking.gmail_client import apply_label_to_emails

        labeled_ids = [email["id"] for email, triage in triage_results if triage.classification != "noise"]
        if labeled_ids:
            labeled_count = asyncio.run(apply_label_to_emails(labeled_ids))
            if labeled_count:
                console.print(f"  Labeled {labeled_count} emails with 'ap-track'")

    # 8. Detect ghosting
    ghosted_count = 0
    if not dry_run:
        ghosted_count = detect_ghosted(applied_jobs, ghosted_days=ghosted_days)

    # 9. Generate markdown docs
    matched_count = counters["matched"]
    if not dry_run:
        doc_count = 0
        for job in applied_jobs:
            if job.get("tracking_status") or matched_count > 0:
                job_dto = job_repo.get_by_url(job["url"])
                if job_dto and job_dto.tracking_status:
                    row_dict = dataclasses.asdict(job_dto)
                    path = generate_tracking_doc(row_dict)
                    if path:
                        update_job_tracking_fields(job["url"], {"tracking_doc_path": path})
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
    tracking_stats = get_tracking_stats()
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
