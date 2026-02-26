"""Smart tailoring: iterative resume optimization with bullet bank and quality gates."""

from applypilot.tailoring.bullet_bank import BulletBank
from applypilot.tailoring.models import (
    Bullet,
    BulletVariant,
    GateResult,
    Resume,
    TailoringResult,
)
from applypilot.tailoring.quality_gates import MetricsGate, QualityGate, RelevanceGate
from applypilot.tailoring.state_machine import SmartTailoringEngine
from applypilot.tailoring.comprehensive_engine import ComprehensiveTailoringEngine

__all__ = [
    "Bullet",
    "BulletBank",
    "BulletVariant",
    "GateResult",
    "MetricsGate",
    "QualityGate",
    "RelevanceGate",
    "Resume",
    "SmartTailoringEngine",
    "ComprehensiveTailoringEngine",
    "TailoringResult",
]