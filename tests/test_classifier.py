"""Tests for apply classifier — destination classifier and redirect resolver."""

import pytest
from applypilot.apply.classifier.models import ApplyTier, ClassificationResult, RedirectChain
from applypilot.apply.classifier.destination_classifier import classify
from applypilot.apply.classifier.constants import ATS_DOMAINS


class TestDestinationClassifier:
    def _chain(self, url, dom=None):
        return RedirectChain(original_url=url, final_url=url, final_dom=dom or "", hops=[])

    def test_dead_link_circular(self):
        chain = RedirectChain(
            original_url="http://x.com", final_url="http://x.com", final_dom="", hops=[], circular_detected=True
        )
        result = classify(chain)
        assert result.tier == ApplyTier.T0_NOT_APPLYABLE

    def test_mailto_is_t6(self):
        result = classify(self._chain("mailto:hr@example.com"))
        assert result.tier == ApplyTier.T6_EMAIL_APPLY

    def test_known_ats_is_t4(self):
        for domain in list(ATS_DOMAINS.keys())[:3]:
            result = classify(self._chain(f"https://{domain}/jobs/123", dom=domain))
            assert result.tier == ApplyTier.T4_ATS_PLATFORM, f"Expected T4 for {domain}"

    def test_google_forms_is_t5(self):
        result = classify(self._chain("https://docs.google.com/forms/d/abc123"))
        assert result.tier == ApplyTier.T5_FORM_SERVICE

    def test_unknown_is_t7(self):
        result = classify(self._chain("https://random-company.com/apply"), page_text="apply now")
        assert result.tier in (ApplyTier.T1_DIRECT_APPLY, ApplyTier.T7_UNKNOWN_FORM)

    def test_login_detected_is_t2(self):
        result = classify(self._chain("https://example.com/login"), page_text="sign in to continue password")
        assert result.tier == ApplyTier.T2_PORTAL_LOGIN

    def test_captcha_detected_is_t3(self):
        result = classify(self._chain("https://example.com/apply"), page_text="recaptcha challenge")
        assert result.tier == ApplyTier.T3_CAPTCHA_MFA


class TestClassificationResult:
    def test_fields(self):
        r = ClassificationResult(tier=ApplyTier.T1_DIRECT_APPLY, confidence=0.9, handler_key="t1_direct")
        assert r.tier == ApplyTier.T1_DIRECT_APPLY
        assert r.confidence == 0.9


class TestRedirectChain:
    def test_defaults(self):
        chain = RedirectChain(
            original_url="http://a.com",
            final_url="http://b.com",
            final_dom="b.com",
            hops=["http://a.com", "http://b.com"],
        )
        assert chain.circular_detected is False
        assert len(chain.hops) == 2
