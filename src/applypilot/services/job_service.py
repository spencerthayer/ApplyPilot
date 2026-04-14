"""JobService — wraps discovery and enrichment, delegates DB to job_repo."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from applypilot.db.dto import JobDTO
from applypilot.db.interfaces.job_repository import JobRepository
from applypilot.services.base import ServiceResult

log = logging.getLogger(__name__)


class JobService:
    def __init__(self, job_repo: JobRepository):
        self._job_repo = job_repo

    def add_single(self, url: str) -> ServiceResult:
        """Insert a single job URL into the DB for the single-job pipeline."""
        existing = self._job_repo.get_by_url(url)
        if existing:
            return ServiceResult(
                success=False,
                error=f"Already in DB: {existing.title or 'untitled'} (score={existing.fit_score})",
            )
        slug_title = url.rstrip("/").split("/")[-1].replace("-", " ").replace("_", " ")
        title = self._fetch_page_title(url) or slug_title

        job = JobDTO(
            url=url,
            title=title,
            site="manual",
            strategy="manual",
            discovered_at=datetime.now(timezone.utc).isoformat(),
        )
        self._job_repo.upsert(job)
        return ServiceResult(data={"title": title})

    @staticmethod
    def _fetch_page_title(url: str) -> str | None:
        """Best-effort title extraction from page HTML."""
        try:
            import urllib.request
            import re

            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read(50000).decode("utf-8", errors="ignore")
            m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
            if m:
                title = m.group(1).strip()
                title = title.split("|")[0].strip().split(" - ")[0].strip()
                if "linkedin.com" in url:
                    _bad = {"linkedin", "login", "sign in", "log in"}
                    if any(b in title.lower() for b in _bad) or len(title.split()) <= 2:
                        return None
                if title and len(title) > 3 and not title.isdigit():
                    return title
        except Exception:
            pass
        return None

    def get_by_url(self, url: str) -> JobDTO | None:
        return self._job_repo.get_by_url(url)

    def get_pending(self, stage: str, limit: int = 0) -> list[JobDTO]:
        return self._job_repo.get_by_stage(stage, limit=limit)

    def get_stats(self) -> ServiceResult:
        """Pipeline statistics."""
        try:
            return ServiceResult(data=self._job_repo.get_stats())
        except Exception as e:
            return ServiceResult(success=False, error=str(e))

    def run_discovery(
            self,
            *,
            workers: int = 1,
            sources: list[str] | None = None,
            companies: list[str] | None = None,
            strict_title: bool = False,
    ) -> ServiceResult:
        """Run all discovery sources with optional source/company filtering."""
        stats: dict = {}
        runners = _get_discovery_runners()

        # Resolve companies via registry
        runner_filters = None
        company_records = None
        if companies:
            from applypilot.discovery.company_registry import get_registry

            registry = get_registry()
            resolved, unresolved = registry.resolve_many(companies)
            for name in unresolved:
                log.warning(
                    "Unknown company: '%s' — not in registry. "
                    "Add it to ~/.applypilot/companies.yaml:\n"
                    "  %s:\n"
                    "    name: \"%s\"\n"
                    "    career_url: https://careers.example.com  # ← replace with actual URL\n"
                    "    runners:\n"
                    "      workday: \"%s\"    # ← or greenhouse/lever/ashby slug, delete if N/A",
                    name, name.lower().replace(" ", "_"), name, name.lower().replace(" ", "_"),
                )
            if resolved:
                log.info(
                    "Company registry: %d resolved → %s",
                    len(resolved),
                    ", ".join(f"{r.name} ({','.join(r.runners)})" for r in resolved),
                )
            company_records = resolved  # noqa: F841 — reserved for future JobSpy post-filter
            # Build per-runner key filters: {"workday": ["walmart"], "greenhouse": ["stripe"]}
            runner_filters = {}
            for rec in resolved:
                for runner_name, runner_key in rec.runners.items():
                    runner_filters.setdefault(runner_name, []).append(runner_key)

            _emit_discovery(
                "company_resolved",
                {
                    "requested": companies,
                    "resolved": [r.key for r in resolved],
                    "unresolved": unresolved,
                    "runner_filters": {k: v for k, v in runner_filters.items()},
                },
            )

        # Source filter
        if sources:
            runners = {k: v for k, v in runners.items() if k in sources}
            _emit_discovery(
                "source_filter_applied",
                {
                    "requested": sources,
                    "executed": list(runners.keys()),
                },
            )

        for source, runner in runners.items():
            try:
                # Determine what to pass this runner
                if runner_filters is not None:
                    if source in ("jobspy", "hackernews"):
                        # These runners can't filter by employer key — skip when --company is set
                        log.info("Skipping %s (--company set, not employer-filterable)", source)
                        stats[source] = "skipped"
                        continue
                    elif source == "smartextract":
                        employer_keys = runner_filters.get(source)
                        if not employer_keys:
                            log.info("Skipping smartextract (no companies mapped)")
                            stats[source] = "skipped"
                            continue
                        runner(workers=workers, employer_keys=employer_keys, strict_title=strict_title)
                    elif source in runner_filters:
                        runner(workers=workers, employer_keys=runner_filters[source], strict_title=strict_title)
                    else:
                        log.info("Skipping %s (no companies mapped to this runner)", source)
                        stats[source] = "skipped"
                        continue
                else:
                    runner(workers=workers, strict_title=strict_title)
                stats[source] = "ok"
            except Exception as e:
                log.error("%s failed: %s", source, e)
                stats[source] = f"error: {e}"

        errors = [k for k, v in stats.items() if str(v).startswith("error")]
        ok = not errors or len(errors) < len(stats)
        return ServiceResult(success=ok, data=stats)

    def run_enrichment(self, *, workers: int = 1, job_url: str | None = None) -> ServiceResult:
        """Run detail enrichment."""
        try:
            from applypilot.enrichment.orchestrator import run_enrichment

            run_enrichment(self._job_repo, workers=workers, job_url=job_url)
            return ServiceResult(data={"action": "enrichment_complete"})
        except Exception as e:
            log.exception("Enrichment failed: %s", e)
            return ServiceResult(success=False, error=str(e))


def _emit_discovery(event_type: str, detail: dict) -> None:
    """Emit analytics event for discovery stage."""
    try:
        import json
        from applypilot.analytics.events import emit
        from applypilot.bootstrap import get_app

        emit("discover", event_type, json.dumps(detail), get_app().container.analytics_repo)
    except Exception:
        pass


def _get_discovery_runners() -> dict:
    """Lazy-load discovery runners to avoid import-time side effects."""

    def _jobspy(*, workers=1, company_records=None, strict_title=False, **_):
        from applypilot.discovery.jobspy import run_discovery

        run_discovery(sites_override=None, company_records=company_records, strict_title=strict_title)

    def _workday(*, workers=1, employer_keys=None, strict_title=False, **_):
        from applypilot.discovery.workday import run_workday_discovery

        run_workday_discovery(workers=workers, employer_keys=employer_keys, strict_title=strict_title)

    def _smartextract(*, workers=1, employer_keys=None, strict_title=False, **_):
        from applypilot.discovery.smartextract import run_smart_extract

        run_smart_extract(workers=workers, employer_keys=employer_keys)

    def _hackernews(*, workers=1, **_):
        from applypilot.discovery.hackernews import run_hn_discovery

        run_hn_discovery()

    def _greenhouse(*, workers=1, employer_keys=None, strict_title=False, **_):
        from applypilot.discovery.greenhouse import search_all

        search_all("", workers=workers, employer_keys=employer_keys, strict_title=strict_title)

    def _ashby(*, workers=1, employer_keys=None, strict_title=False, **_):
        from applypilot.discovery.ashby.runner import run_ashby_discovery

        run_ashby_discovery(workers=workers, employer_keys=employer_keys, strict_title=strict_title)

    def _lever(*, workers=1, employer_keys=None, strict_title=False, **_):
        from applypilot.discovery.lever.runner import run_lever_discovery

        run_lever_discovery(workers=workers, employer_keys=employer_keys, strict_title=strict_title)

    return {
        "jobspy": _jobspy,
        "workday": _workday,
        "smartextract": _smartextract,
        "hackernews": _hackernews,
        "greenhouse": _greenhouse,
        "ashby": _ashby,
        "lever": _lever,
    }
