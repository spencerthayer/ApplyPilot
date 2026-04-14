"""Lever discovery runner — wires into the main pipeline.

Loads employer registry from config, fetches jobs via Lever API,
applies title/location filters, stores results.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import yaml

from applypilot.config.paths import APP_DIR, CONFIG_DIR
from applypilot.discovery.title_filter import title_matches_query

log = logging.getLogger(__name__)


def _load_employers() -> dict:
    """Load Lever employer registry. User override → package default."""
    for path in [APP_DIR / "lever.yaml", CONFIG_DIR / "lever.yaml"]:
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return data.get("employers", {})
    return {}


def run_lever_discovery(
        workers: int = 1,
        employer_keys: list[str] | None = None,
        strict_title: bool = False,
) -> dict:
    """Main entry point for Lever-based job discovery."""
    from applypilot.discovery.lever import fetch_jobs, parse_jobs
    from applypilot.bootstrap import get_app
    from applypilot.db.dto import JobDTO
    from applypilot import config

    employers = _load_employers()
    if not employers:
        log.info("No Lever employers configured")
        return {"found": 0, "new": 0, "existing": 0}

    if employer_keys is not None:
        employers = {k: v for k, v in employers.items() if k in employer_keys}
        if not employers:
            log.info("No Lever employers match requested companies")
            return {"found": 0, "new": 0, "existing": 0}

    search_cfg = config.load_search_config()
    queries = [q["query"] for q in search_cfg.get("queries", [])]
    job_repo = get_app().container.job_repo
    now = datetime.now(timezone.utc).isoformat()

    total_new, total_existing, total_found = 0, 0, 0

    for slug, emp in employers.items():
        name = emp.get("name", slug) if isinstance(emp, dict) else slug
        raw_jobs = fetch_jobs(slug)
        if raw_jobs is None:
            continue
        jobs = parse_jobs(raw_jobs, name)
        total_found += len(jobs)

        pollution_all_filtered = len(jobs) > 0
        for job in jobs:
            title = job.get("title", "")
            query_match = not queries or any(title_matches_query(title, q, strict=strict_title) for q in queries)
            if not query_match:
                loose_match = any(title_matches_query(title, q, strict=False) for q in queries) if queries else False
                if not loose_match:
                    continue
            pollution_all_filtered = False
            is_suggested = query_match is False

            url = job.get("url", "")
            if not url or job_repo.get_by_url(url):
                total_existing += 1
                continue
            strategy = "lever_api_suggested" if is_suggested else "lever_api"
            job_repo.upsert(
                JobDTO(
                    url=url,
                    title=title,
                    location=job.get("location"),
                    description=job.get("description"),
                    site=f"lever: {name}",
                    strategy=strategy,
                    discovered_at=now,
                    full_description=job.get("full_description"),
                    detail_scraped_at=now if job.get("full_description") else None,
                )
            )
            total_new += 1

        if pollution_all_filtered and jobs:
            log.warning(
                "No relevant results from %s — portal may be returning suggestions (got %d, all filtered by title)",
                name,
                len(jobs),
            )

    log.info("Lever discovery: %d found, %d new, %d existing", total_found, total_new, total_existing)
    return {"found": total_found, "new": total_new, "existing": total_existing}
