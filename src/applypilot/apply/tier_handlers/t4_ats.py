"""T4: ATS Platform — known ATS-specific handlers (Workday, Greenhouse, Lever, etc).

Routes to ATS-specific logic when available, falls back to generic browser agent.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from applypilot.apply.tier_handlers.base import TierHandler
from applypilot.db.dto import ApplyResultDTO

log = logging.getLogger(__name__)


class T4AtsHandler(TierHandler):
    """T4: Known ATS platform — use ATS-specific handler or generic agent."""

    def handle(self, job, chain, classification, resume_path, profile) -> ApplyResultDTO:
        url = chain.final_url or job.url
        ats_name = classification.evidence.get("ats", "unknown")
        log.info("[T4] ATS platform (%s): %s", ats_name, url[:80])

        # Route to ATS-specific handler if available
        match ats_name:
            case "workday":
                return self._handle_workday(job, url, resume_path, profile)
            case "greenhouse":
                return self._handle_greenhouse(job, url, resume_path, profile)
            case _:
                return self._handle_generic_ats(job, url, resume_path, profile, ats_name)

    def _handle_workday(self, job, url, resume_path, profile) -> ApplyResultDTO:
        """Workday portals typically require login — escalate to T2 path."""
        return ApplyResultDTO(
            url=job.url,
            apply_status="needs_human",
            apply_error="T4: Workday portal — login/CAPTCHA typically required",
        )

    def _handle_greenhouse(self, job, url, resume_path, profile) -> ApplyResultDTO:
        """Greenhouse boards often have direct apply forms."""
        try:
            from applypilot.apply.native_agent import run_apply_agent

            result, duration_ms = run_apply_agent(
                job_url=url,
                resume_path=resume_path,
                profile=profile,
            )
            if result == "applied":
                return ApplyResultDTO(
                    url=job.url,
                    apply_status="applied",
                    applied_at=datetime.now(timezone.utc).isoformat(),
                    apply_duration_ms=duration_ms,
                )
            return ApplyResultDTO(
                url=job.url,
                apply_status="failed",
                apply_error=f"T4/greenhouse: agent returned {result}",
                apply_duration_ms=duration_ms,
            )
        except Exception as e:
            return ApplyResultDTO(
                url=job.url,
                apply_status="failed",
                apply_error=f"T4/greenhouse: {e}",
            )

    def _handle_generic_ats(self, job, url, resume_path, profile, ats_name) -> ApplyResultDTO:
        """Generic ATS — try browser agent, escalate on failure."""
        try:
            from applypilot.apply.native_agent import run_apply_agent

            result, duration_ms = run_apply_agent(
                job_url=url,
                resume_path=resume_path,
                profile=profile,
            )
            if result == "applied":
                return ApplyResultDTO(
                    url=job.url,
                    apply_status="applied",
                    applied_at=datetime.now(timezone.utc).isoformat(),
                    apply_duration_ms=duration_ms,
                )
            return ApplyResultDTO(
                url=job.url,
                apply_status="needs_human",
                apply_error=f"T4/{ats_name}: agent returned {result}",
                apply_duration_ms=duration_ms,
            )
        except Exception as e:
            return ApplyResultDTO(
                url=job.url,
                apply_status="needs_human",
                apply_error=f"T4/{ats_name}: {e}",
            )
