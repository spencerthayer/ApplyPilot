"""Validation data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationResult:
    """Result of a single validation check."""

    passed: bool
    check_name: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    retry_instructions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_retry_prompt(self) -> str:
        if self.passed:
            return ""
        lines = [f"## Fix Required: {self.check_name}", "", "### Issues Found:"]
        for error in self.errors:
            lines.append(f"- {error}")
        if self.warnings:
            lines.extend(["", "### Warnings:"])
            for w in self.warnings:
                lines.append(f"- {w}")
        lines.extend(["", "### Specific Instructions:"])
        for inst in self.retry_instructions:
            lines.append(f"- {inst}")
        return "\n".join(lines)


@dataclass
class ValidationConfig:
    """Configuration for validation checks."""

    enabled: bool = True
    max_retries: int = 3
    min_bullets_per_role: int = 2
    max_bullets_per_role: int = 5
    min_total_bullets_senior: int = 15
    max_total_bullets_senior: int = 25
    min_total_bullets_mid: int = 12
    max_total_bullets_mid: int = 20
    min_total_bullets_junior: int = 8
    max_total_bullets_junior: int = 15
    min_metrics_ratio: float = 0.7
    weak_verbs: list[str] = field(
        default_factory=lambda: [
            "responsible for",
            "assisted with",
            "helped with",
            "worked on",
            "involved in",
            "participated in",
            "contributed to",
        ]
    )
    metric_patterns: list[str] = field(
        default_factory=lambda: [
            r"\d+%",
            r"\$\d",
            r"\d+x",
            r"\d+\s*(?:hours?|days?|weeks?|months?|years?)",
            r"\d+\s*(?:k|k\+|million|m|billion|b)?\s+(?:users?|customers?|requests?|transactions?)",
        ]
    )

    @classmethod
    def from_config(cls, config: dict) -> ValidationConfig:
        v = config.get("validation", {})
        defaults = cls()
        return cls(
            enabled=v.get("enabled", True),
            max_retries=v.get("max_retries", 3),
            min_bullets_per_role=v.get("min_bullets_per_role", 2),
            max_bullets_per_role=v.get("max_bullets_per_role", 5),
            min_total_bullets_senior=v.get("min_total_bullets_senior", 15),
            max_total_bullets_senior=v.get("max_total_bullets_senior", 25),
            min_total_bullets_mid=v.get("min_total_bullets_mid", 12),
            max_total_bullets_mid=v.get("max_total_bullets_mid", 20),
            min_total_bullets_junior=v.get("min_total_bullets_junior", 8),
            max_total_bullets_junior=v.get("max_total_bullets_junior", 15),
            min_metrics_ratio=v.get("min_metrics_ratio", 0.7),
            weak_verbs=v.get("weak_verbs", defaults.weak_verbs),
            metric_patterns=v.get("metric_patterns", defaults.metric_patterns),
        )
