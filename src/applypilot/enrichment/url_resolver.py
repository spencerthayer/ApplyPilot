"""URL resolution — resolves relative URLs to absolute using site base URLs.

Uses JobRepository for all DB access. No raw SQL.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urljoin

from applypilot.db.interfaces.job_repository import JobRepository
from applypilot.url_safety import is_algolia_queries_url

log = logging.getLogger(__name__)


def _load_base_urls() -> dict[str, str | None]:
    """Load site base URLs from config/sites.yaml."""
    from applypilot.config import load_base_urls

    return load_base_urls()


def resolve_url(raw_url: str, site: str) -> str | None:
    """Resolve a stored URL to an absolute URL."""
    if not raw_url:
        return None
    if raw_url.startswith("http://") or raw_url.startswith("https://"):
        return raw_url
    if site == "WelcomeToTheJungle":
        return None
    if site == "Randstad Canada" and "/" not in raw_url:
        return f"https://www.randstad.ca/jobs/search/{raw_url}"
    if site == "4DayWeek" and raw_url in ("/", "/jobs"):
        return None

    base = _load_base_urls().get(site)
    if not base:
        return None

    if ";jsessionid=" in raw_url:
        raw_url = raw_url.split(";jsessionid=")[0]

    return urljoin(base, raw_url)


def resolve_all_urls(job_repo: JobRepository) -> dict:
    """Resolve all relative URLs in the database. Returns stats."""
    jobs = job_repo.get_all_urls_and_sites()
    resolved = 0
    failed = 0
    already_absolute = 0

    for url, site in jobs:
        if url.startswith("http://") or url.startswith("https://"):
            already_absolute += 1
            continue
        new_url = resolve_url(url, site)
        if new_url and new_url != url:
            if job_repo.update_url(url, new_url):
                resolved += 1
            else:
                job_repo.delete(url)
                resolved += 1
        else:
            failed += 1

    # Also resolve relative application_urls
    app_resolved = 0
    app_jobs = job_repo.get_relative_application_urls()
    for url, site, app_url in app_jobs:
        new_app = resolve_url(app_url, site)
        if new_app and new_app != app_url:
            job_repo.update_application_url(url, new_app)
            app_resolved += 1

    return {
        "resolved": resolved,
        "failed": failed,
        "already_absolute": already_absolute,
        "app_resolved": app_resolved,
    }


def resolve_wttj_urls(job_repo: JobRepository, stealth_init_script: str, ua: str) -> int:
    """Re-fetch WTTJ Algolia API to get proper detail URLs.

    Returns count of URLs updated.
    """
    from playwright.sync_api import sync_playwright

    wttj_jobs = job_repo.get_wttj_jobs()
    if not wttj_jobs:
        return 0

    algolia_data: dict = {}

    def capture_algolia(response):
        if is_algolia_queries_url(response.url):
            try:
                algolia_data["response"] = json.loads(response.text())
            except Exception:
                pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=ua)
        context.add_init_script(stealth_init_script)
        page = context.new_page()
        page.on("response", capture_algolia)
        page.goto(
            "https://www.welcometothejungle.com/en/jobs?query=developer&refinementList%5Bremote%5D%5B%5D=fulltime",
            timeout=60000,
        )
        page.wait_for_load_state("networkidle")
        browser.close()

    if not algolia_data.get("response"):
        log.warning("WTTJ: No Algolia response captured")
        return 0

    results = algolia_data["response"].get("results", [])
    slug_map: dict = {}
    for rs in results:
        for hit in rs.get("hits", []):
            slug = hit.get("slug", "")
            org = hit.get("organization", {})
            org_slug = org.get("slug", "") if isinstance(org, dict) else ""
            name = hit.get("name", "")
            if slug and org_slug:
                detail_url = f"https://www.welcometothejungle.com/en/companies/{org_slug}/jobs/{slug}"
                slug_map[slug] = {"url": detail_url, "name": name}

    updated = 0
    for old_url, _old_title in wttj_jobs:
        slug = old_url.split("_DFNS_")[0] if "_DFNS_" in old_url else old_url
        match = slug_map.get(slug) or slug_map.get(old_url)
        if match:
            if not job_repo.update_url(old_url, match["url"]):
                job_repo.delete(old_url)
            updated += 1
        else:
            for s, data in slug_map.items():
                if s in old_url or old_url in s:
                    if not job_repo.update_url(old_url, data["url"]):
                        job_repo.delete(old_url)
                    updated += 1
                    break
    return updated
