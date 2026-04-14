"""T2: Portal Login — session manager + Playwright.

Detects login walls, attempts stored credentials, escalates to HITL if needed.
"""

from __future__ import annotations

import logging

from applypilot.apply.tier_handlers.base import TierHandler
from applypilot.db.dto import ApplyResultDTO

log = logging.getLogger(__name__)


class T2PortalLoginHandler(TierHandler):
    """T2: Portal login — try stored credentials, then HITL."""

    def handle(self, job, chain, classification, resume_path, profile) -> ApplyResultDTO:
        url = chain.final_url or job.url
        log.info("[T2] Portal login required: %s", url[:80])

        # Check for stored account credentials
        try:
            from applypilot.bootstrap import get_app

            account_repo = get_app().container.account_repo
            from urllib.parse import urlparse

            domain = urlparse(url).netloc
            accounts = account_repo.get_by_domain(domain)

            if accounts:
                log.info("[T2] Found stored credentials for %s, attempting login", domain)
                return self._attempt_with_credentials(job, url, resume_path, profile, accounts[0])
        except Exception as e:
            log.debug("[T2] Credential lookup failed: %s", e)

        # No credentials — escalate to human
        return ApplyResultDTO(
            url=job.url,
            apply_status="needs_human",
            apply_error=f"T2: login required at {url[:80]} — no stored credentials",
        )

    def _attempt_with_credentials(self, job, url, resume_path, profile, account) -> ApplyResultDTO:
        """Try applying with stored credentials via browser agent."""
        try:
            from applypilot.apply.native_agent import run_apply_agent

            result, duration_ms = run_apply_agent(
                job_url=url,
                resume_path=resume_path,
                profile=profile,
                credentials={"email": account.email, "password": account.password},
            )
            if result == "applied":
                from datetime import datetime, timezone

                return ApplyResultDTO(
                    url=job.url,
                    apply_status="applied",
                    applied_at=datetime.now(timezone.utc).isoformat(),
                    apply_duration_ms=duration_ms,
                )
            return ApplyResultDTO(
                url=job.url,
                apply_status="failed",
                apply_error=f"T2: login attempt returned {result}",
                apply_duration_ms=duration_ms,
            )
        except Exception as e:
            return ApplyResultDTO(
                url=job.url,
                apply_status="needs_human",
                apply_error=f"T2: login failed — {e}",
            )
