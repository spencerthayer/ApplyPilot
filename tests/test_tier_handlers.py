"""Tests for tier handlers and rate limiter."""

import pytest
from applypilot.apply.classifier.models import ApplyTier, ClassificationResult, RedirectChain
from applypilot.apply.tier_handlers.registry import T0SkipHandler, T6EmailHandler
from applypilot.apply.rate_limiter import PortalRateLimiter


class TestT0SkipHandler:
    def test_returns_failed(self):
        from applypilot.db.dto import JobDTO

        job = JobDTO(url="http://dead.link")
        chain = RedirectChain(original_url="http://dead.link", final_url="http://dead.link", final_dom="", hops=[])
        classification = ClassificationResult(
            tier=ApplyTier.T0_NOT_APPLYABLE, confidence=1.0, handler_key="t0_skip", evidence={"reason": "404"}
        )
        handler = T0SkipHandler()
        result = handler.handle(job, chain, classification, None, None)
        assert result.apply_status == "failed"
        assert "T0" in result.apply_error


class TestT6EmailHandler:
    def test_returns_needs_human(self):
        from applypilot.db.dto import JobDTO

        job = JobDTO(url="mailto:hr@co.com")
        chain = RedirectChain(original_url="mailto:hr@co.com", final_url="mailto:hr@co.com", final_dom="", hops=[])
        classification = ClassificationResult(
            tier=ApplyTier.T6_EMAIL_APPLY, confidence=1.0, handler_key="t6_email", evidence={"email": "hr@co.com"}
        )
        handler = T6EmailHandler()
        result = handler.handle(job, chain, classification, None, None)
        assert result.apply_status == "needs_human"


class TestRateLimiter:
    def test_allows_within_limit(self):
        rl = PortalRateLimiter(max_per_hour=5)
        wait = rl.acquire("indeed.com")
        assert wait == 0

    def test_blocks_when_exhausted(self):
        rl = PortalRateLimiter(max_per_hour=1, jitter_seconds=0)
        assert rl.acquire("indeed.com") == 0
        assert rl.acquire("indeed.com") > 0
