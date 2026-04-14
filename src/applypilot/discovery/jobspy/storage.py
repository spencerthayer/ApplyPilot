"""Storage."""

from applypilot.db.dto import JobDTO

__all__ = ["_JOBSPY_PARAMS", "store_jobspy_results"]

"""JobSpy-based job discovery: searches Indeed, LinkedIn, Glassdoor, ZipRecruiter.

Uses python-jobspy to scrape multiple job boards, deduplicates results,
parses salary ranges, and stores everything in the ApplyPilot database.

Search queries, locations, and filtering rules are loaded from the user's
search configuration YAML (searches.yaml) rather than being hardcoded.
"""

import inspect as _inspect
from datetime import datetime, timezone

from jobspy import scrape_jobs as _raw_scrape_jobs

# Only pass params that the installed jobspy version actually accepts.
_JOBSPY_PARAMS = set(_inspect.signature(_raw_scrape_jobs).parameters.keys())

from applypilot.discovery.jobspy.debug import _clean


def _title_matches_query(title: str, query: str, *, strict: bool = False) -> bool:
    """Check if job title is relevant to search query — delegates to shared filter."""
    from applypilot.discovery.title_filter import title_matches_query

    return title_matches_query(title, query, strict=strict)


def store_jobspy_results(
        job_repo, df, source_label: str, company_records=None, strict_title: bool = False
) -> tuple[int, int]:
    """Store JobSpy DataFrame results into the DB. Returns (new, existing)."""
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    existing = 0
    skipped_title = 0
    skipped_company = 0
    skipped_relevance = 0

    for _, row in df.iterrows():
        url = str(row.get("job_url", ""))
        if not url or url == "nan":
            continue

        if job_repo.get_by_url(url):
            existing += 1
            continue

        title = _clean(row.get("title"))

        # Title relevance filter — skip jobs that don't match the search query
        if title and source_label and not _title_matches_query(title, source_label, strict=strict_title):
            skipped_title += 1
            continue

        # Profile-driven relevance gate
        from applypilot.discovery.relevance_gate import is_relevant

        location_str = _clean(row.get("location"))
        description = _clean(row.get("description"))
        if not is_relevant(title or "", location_str or "", description or ""):
            skipped_relevance += 1
            continue

        # Company post-filter — match scraped company name against registry aliases
        if company_records:
            scraped_company = _clean(row.get("company")) or ""
            if scraped_company:
                from applypilot.discovery.company_registry import get_registry

                registry = get_registry()
                if not any(registry.matches_scraped_name(scraped_company, rec) for rec in company_records):
                    skipped_company += 1
                    continue

        location_str = _clean(row.get("location"))

        salary = None
        min_amt = _clean(row.get("min_amount"))
        max_amt = _clean(row.get("max_amount"))
        interval = _clean(row.get("interval")) or ""
        currency = _clean(row.get("currency")) or ""
        if min_amt:
            if max_amt:
                salary = f"{currency}{int(float(min_amt)):,}-{currency}{int(float(max_amt)):,}"
            else:
                salary = f"{currency}{int(float(min_amt)):,}"
            if interval:
                salary += f"/{interval}"

        description = _clean(row.get("description"))
        site_name = str(row.get("site", source_label))
        is_remote = row.get("is_remote", False)

        site_label = f"{site_name}"
        if is_remote:
            location_str = f"{location_str} (Remote)" if location_str else "Remote"

        full_description = None
        detail_scraped_at = None
        if description and len(description) > 200:
            full_description = description
            detail_scraped_at = now

        apply_url = _clean(row.get("job_url_direct"))

        job_repo.upsert(
            JobDTO(
                url=url,
                title=title,
                salary=salary,
                description=description,
                location=location_str,
                site=site_label,
                strategy="jobspy",
                discovered_at=now,
                full_description=full_description,
                application_url=apply_url,
                detail_scraped_at=detail_scraped_at,
            )
        )
        new += 1
        try:
            from applypilot.analytics.helpers import emit_job_discovered

            emit_job_discovered(url, site_label, title)
        except Exception:
            pass

    return new, existing
