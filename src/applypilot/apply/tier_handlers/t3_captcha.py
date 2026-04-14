"""T3: CAPTCHA/MFA — CapSolver auto-solve + HITL fallback.

Attempts automated CAPTCHA solving via CapSolver API. Falls back to human review.
"""

from __future__ import annotations

import logging
import os

from applypilot.apply.tier_handlers.base import TierHandler
from applypilot.db.dto import ApplyResultDTO

log = logging.getLogger(__name__)


class T3CaptchaHandler(TierHandler):
    """T3: CAPTCHA/MFA — auto-solve or escalate to human."""

    def handle(self, job, chain, classification, resume_path, profile) -> ApplyResultDTO:
        url = chain.final_url or job.url
        captcha_type = classification.evidence.get("indicator", "unknown")
        log.info("[T3] CAPTCHA detected (%s): %s", captcha_type, url[:80])

        capsolver_key = os.environ.get("CAPSOLVER_API_KEY", "").strip()
        if not capsolver_key:
            return ApplyResultDTO(
                url=job.url,
                apply_status="needs_human",
                apply_error=f"T3: {captcha_type} detected, no CAPSOLVER_API_KEY configured",
            )

        # Attempt auto-solve via CapSolver, then continue with browser agent
        try:
            from applypilot.apply.native_agent import run_apply_agent

            result, duration_ms = run_apply_agent(
                job_url=url,
                resume_path=resume_path,
                profile=profile,
                capsolver_key=capsolver_key,
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
                apply_status="needs_human",
                apply_error=f"T3: CAPTCHA solve attempted but agent returned {result}",
                apply_duration_ms=duration_ms,
            )
        except Exception as e:
            log.error("[T3] Failed: %s — %s", url[:60], e)
            return ApplyResultDTO(
                url=job.url,
                apply_status="needs_human",
                apply_error=f"T3: CAPTCHA auto-solve failed — {e}",
            )
