"""T5: Form Service — Google Forms, Typeform, Airtable, JotForm.

Predictable DOM structures — LLM maps questions to profile fields, auto-fills.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from applypilot.apply.tier_handlers.base import TierHandler
from applypilot.db.dto import ApplyResultDTO

log = logging.getLogger(__name__)


class T5FormServiceHandler(TierHandler):
    """T5: Form service — structured form with predictable DOM."""

    def handle(self, job, chain, classification, resume_path, profile) -> ApplyResultDTO:
        url = chain.final_url or job.url
        service = classification.evidence.get("service", "unknown")
        log.info("[T5] Form service (%s): %s", service, url[:80])

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
                apply_error=f"T5/{service}: agent returned {result}",
                apply_duration_ms=duration_ms,
            )
        except Exception as e:
            log.error("[T5] Failed: %s — %s", url[:60], e)
            return ApplyResultDTO(
                url=job.url,
                apply_status="failed",
                apply_error=f"T5/{service}: {e}",
            )
