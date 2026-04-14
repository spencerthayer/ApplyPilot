"""Tiered tailoring dispatcher (LLD §8, INIT-22).

Tailoring effort scales with match score:
  TL0-Skip:    score < 5  → no tailoring, mark as skipped
  TL2-Full:    score 5-8  → two-stage pipeline (planner + generator)
  TL3-Premium: score 9-10 → two-stage + flagged for HITL review
"""

from __future__ import annotations

from enum import StrEnum


class TailoringLevel(StrEnum):
    TL0_SKIP = "tl0_skip"
    TL2_FULL = "tl2_full"
    TL3_PREMIUM = "tl3_premium"


def classify_tailoring_level(fit_score: int | None) -> TailoringLevel:
    """Determine tailoring effort from fit score."""
    score = fit_score or 0
    match score:
        case s if s >= 9:
            return TailoringLevel.TL3_PREMIUM
        case s if s >= 5:
            return TailoringLevel.TL2_FULL
        case _:
            return TailoringLevel.TL0_SKIP
