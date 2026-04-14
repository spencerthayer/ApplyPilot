"""Tier handler registry — maps tier keys to handler instances.

All 8 tiers (T0–T7) are registered. The dispatcher resolves the handler
from the ClassificationResult and delegates.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from applypilot.apply.classifier.models import ClassificationResult, RedirectChain
from applypilot.apply.tier_handlers.base import TierHandler
from applypilot.db.dto import ApplyResultDTO, JobDTO

log = logging.getLogger(__name__)


class T0SkipHandler(TierHandler):
    """T0: Not applyable — skip immediately."""

    def handle(self, job, chain, classification, resume_path, profile) -> ApplyResultDTO:
        return ApplyResultDTO(
            url=job.url,
            apply_status="failed",
            apply_error=f"T0: {classification.evidence.get('reason', 'not applyable')}",
        )


class T6EmailHandler(TierHandler):
    """T6: Email apply — compose email with resume + cover letter."""

    def handle(self, job, chain, classification, resume_path, profile) -> ApplyResultDTO:
        email = classification.evidence.get("email", "unknown")
        return ApplyResultDTO(
            url=job.url,
            apply_status="needs_human",
            apply_error=f"T6: email apply to {email}",
        )


class T7AgenticHandler(TierHandler):
    """T7: Unknown form — delegate to premium-tier browser agent."""

    def handle(self, job, chain, classification, resume_path, profile) -> ApplyResultDTO:
        url = chain.final_url or job.url
        log.info("[T7] Unknown form — agentic mode: %s", url[:80])
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
                apply_error=f"T7: agent returned {result}",
                apply_duration_ms=duration_ms,
            )
        except Exception as e:
            return ApplyResultDTO(
                url=job.url,
                apply_status="needs_human",
                apply_error=f"T7: {e}",
            )


def _build_registry() -> dict[str, TierHandler]:
    """Lazy-build the full registry to avoid import-time side effects."""
    from applypilot.apply.tier_handlers.t1_direct import T1DirectApplyHandler
    from applypilot.apply.tier_handlers.t2_portal import T2PortalLoginHandler
    from applypilot.apply.tier_handlers.t3_captcha import T3CaptchaHandler
    from applypilot.apply.tier_handlers.t4_ats import T4AtsHandler
    from applypilot.apply.tier_handlers.t5_form import T5FormServiceHandler

    return {
        "T0": T0SkipHandler(),
        "T1": T1DirectApplyHandler(),
        "T2": T2PortalLoginHandler(),
        "T3": T3CaptchaHandler(),
        "T4": T4AtsHandler(),
        "T5": T5FormServiceHandler(),
        "T6": T6EmailHandler(),
        "T7": T7AgenticHandler(),
    }


_registry: dict[str, TierHandler] | None = None


def get_handler(tier_key: str) -> TierHandler | None:
    """Get handler for a tier key, or None if not registered."""
    global _registry
    if _registry is None:
        _registry = _build_registry()
    return _registry.get(tier_key)


def dispatch(
        job: JobDTO,
        chain: RedirectChain,
        classification: ClassificationResult,
        resume_path: str,
        profile: dict,
) -> ApplyResultDTO:
    """Classify and dispatch to the appropriate tier handler."""
    handler = get_handler(classification.handler_key)
    if handler is None:
        log.error("No handler for tier %s", classification.handler_key)
        return ApplyResultDTO(
            url=job.url,
            apply_status="failed",
            apply_error=f"No handler for tier {classification.handler_key}",
        )

    log.info(
        "[dispatch] %s → %s (confidence=%.2f)",
        job.url[:60],
        classification.handler_key,
        classification.confidence,
    )
    result = handler.handle(job, chain, classification, resume_path, profile)

    # Emit analytics event
    from applypilot.analytics.helpers import emit_job_applied, emit_apply_failed, emit_apply_needs_human

    match result.apply_status:
        case "applied":
            emit_job_applied(job.url, getattr(job, "site", ""), classification.handler_key, result.apply_duration_ms)
        case "failed":
            emit_apply_failed(job.url, getattr(job, "site", ""), classification.handler_key, result.apply_error or "")
        case "needs_human":
            emit_apply_needs_human(
                job.url, getattr(job, "site", ""), classification.handler_key, result.apply_error or ""
            )

    return result
