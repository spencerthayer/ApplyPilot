"""T1: Direct Apply — open form, no login wall, Playwright auto-fill.

The simplest apply path: navigate to the form, fill fields, upload resume, submit.
Delegates to the existing browser agent for actual form interaction.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from applypilot.apply.tier_handlers.base import TierHandler
from applypilot.db.dto import ApplyResultDTO

log = logging.getLogger(__name__)


class T1DirectApplyHandler(TierHandler):
    """T1: Direct apply — open form, Playwright auto-fill + submit."""

    def handle(self, job, chain, classification, resume_path, profile) -> ApplyResultDTO:
        url = chain.final_url or job.url
        log.info("[T1] Direct apply: %s", url[:80])

        try:
            from applypilot.apply.native_agent import run_apply_agent

            result, duration_ms = run_apply_agent(
                job_url=url,
                resume_path=resume_path,
                profile=profile,
                cover_letter_path=getattr(job, "cover_letter_path", None),
            )
            match result:
                case "applied":
                    return ApplyResultDTO(
                        url=job.url,
                        apply_status="applied",
                        applied_at=datetime.now(timezone.utc).isoformat(),
                        apply_duration_ms=duration_ms,
                    )
                case "needs_human":
                    return ApplyResultDTO(
                        url=job.url,
                        apply_status="needs_human",
                        apply_error="T1: agent needs human assistance",
                    )
                case _:
                    return ApplyResultDTO(
                        url=job.url,
                        apply_status="failed",
                        apply_error=f"T1: agent returned {result}",
                        apply_duration_ms=duration_ms,
                    )
        except Exception as e:
            log.error("[T1] Failed: %s — %s", url[:60], e)
            return ApplyResultDTO(
                url=job.url,
                apply_status="failed",
                apply_error=f"T1: {e}",
            )
