"""Destination classifier — classifies final URL into apply tiers T0-T7.

Decision tree evaluated top-to-bottom:
  dead link → T0, mailto → T6, known ATS → T4, form service → T5,
  CAPTCHA → T3, login → T2, open form → T1, else → T7.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from applypilot.apply.classifier.constants import (
    ATS_DOMAINS,
    CAPTCHA_INDICATORS,
    DEAD_INDICATORS,
    FORM_SERVICE_PATTERNS,
    LOGIN_INDICATORS,
)
from applypilot.apply.classifier.models import ApplyTier, ClassificationResult, RedirectChain

log = logging.getLogger(__name__)


def classify(chain: RedirectChain, page_text: str = "") -> ClassificationResult:
    """Classify a resolved redirect chain into an apply tier.

    Args:
        chain: Resolved redirect chain with final_url and final_dom.
        page_text: Optional page text content for content-based classification.

    Returns:
        ClassificationResult with tier, confidence, and handler key.
    """
    url = chain.final_url
    domain = chain.final_dom or urlparse(url).netloc
    text_lower = page_text.lower()

    # T0: Dead link
    if chain.circular_detected:
        return ClassificationResult(ApplyTier.T0_NOT_APPLYABLE, 0.95, "T0", {"reason": "circular_redirect"})
    for indicator in DEAD_INDICATORS:
        if indicator in text_lower:
            return ClassificationResult(ApplyTier.T0_NOT_APPLYABLE, 0.90, "T0", {"indicator": indicator})

    # T6: Email apply (mailto:)
    if url.startswith("mailto:"):
        return ClassificationResult(ApplyTier.T6_EMAIL_APPLY, 0.99, "T6", {"email": url[7:]})

    # T4: Known ATS platform
    for ats_domain, ats_name in ATS_DOMAINS.items():
        if ats_domain in domain:
            return ClassificationResult(ApplyTier.T4_ATS_PLATFORM, 0.95, "T4", {"ats": ats_name})

    # T5: Form service
    for pattern in FORM_SERVICE_PATTERNS:
        if pattern in url:
            return ClassificationResult(ApplyTier.T5_FORM_SERVICE, 0.90, "T5", {"service": pattern})

    # T3: CAPTCHA/MFA detected
    for indicator in CAPTCHA_INDICATORS:
        if indicator in text_lower:
            return ClassificationResult(ApplyTier.T3_CAPTCHA_MFA, 0.80, "T3", {"indicator": indicator})

    # T2: Login required
    for indicator in LOGIN_INDICATORS:
        if indicator in text_lower:
            return ClassificationResult(ApplyTier.T2_PORTAL_LOGIN, 0.75, "T2", {"indicator": indicator})

    # T1: Direct apply (open form, no barriers detected)
    if any(kw in text_lower for kw in ("apply now", "submit application", "apply for this")):
        return ClassificationResult(ApplyTier.T1_DIRECT_APPLY, 0.70, "T1", {})

    # T7: Unknown form
    return ClassificationResult(ApplyTier.T7_UNKNOWN_FORM, 0.50, "T7", {})
