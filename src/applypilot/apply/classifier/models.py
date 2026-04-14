"""Apply tier models and constants."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ApplyTier(Enum):
    T0_NOT_APPLYABLE = "T0"
    T1_DIRECT_APPLY = "T1"
    T2_PORTAL_LOGIN = "T2"
    T3_CAPTCHA_MFA = "T3"
    T4_ATS_PLATFORM = "T4"
    T5_FORM_SERVICE = "T5"
    T6_EMAIL_APPLY = "T6"
    T7_UNKNOWN_FORM = "T7"


@dataclass(frozen=True)
class RedirectHop:
    url: str
    status_code: int | None = None
    redirect_type: str = "http"
    elapsed_ms: int = 0


@dataclass
class RedirectChain:
    original_url: str
    final_url: str
    hops: list[RedirectHop] = field(default_factory=list)
    final_dom: str | None = None
    total_time_ms: int = 0
    circular_detected: bool = False


@dataclass(frozen=True)
class ClassificationResult:
    tier: ApplyTier
    confidence: float
    handler_key: str
    evidence: dict = field(default_factory=dict)
