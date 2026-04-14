"""RuntimeConfig — unified configuration with 3-level precedence.

Precedence: per-call override > config.yaml > hardcoded default.

Built via RuntimeConfig.load(), frozen, injected into services via DI.
No global mutable config state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScoringConfig:
    min_score: int = 7
    max_attempts_per_job: int = 3
    attempt_backoff_seconds: float = 1.0


@dataclass(frozen=True)
class TailoringConfig:
    max_attempts: int = 5
    validation_mode: str = "normal"
    # Statistical guardrail thresholds (LLD §7, INIT-14a)
    retention_threshold: float = 0.40  # resume tailoring
    track_retention_threshold: float = 0.60  # track resume generation
    variant_retention_threshold: float = 0.70  # variant generation
    combo_retention_threshold: float = 0.50  # combo role blending
    # Semantic guardrail thresholds (LLD §7, INIT-14b)
    cover_letter_fabrication_threshold: float = 0.15
    profile_enrichment_fabrication_threshold: float = 0.0  # zero tolerance


@dataclass(frozen=True)
class ApplyConfig:
    max_attempts: int = 10
    timeout_seconds: int = 300
    poll_interval_seconds: int = 30
    rate_limit_per_hour: int = 5
    viewport: str = "1280x900"


@dataclass(frozen=True)
class PipelineConfig:
    chunk_size: int = 1000
    enrichment_limit_per_site: int = 100
    cost_tracking: bool = True


@dataclass(frozen=True)
class RuntimeConfig:
    """Immutable runtime configuration. All tunables in one place."""

    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    tailoring: TailoringConfig = field(default_factory=TailoringConfig)
    apply: ApplyConfig = field(default_factory=ApplyConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)

    @classmethod
    def load(cls, config_path: Path | None = None) -> RuntimeConfig:
        """Load config from YAML file, falling back to defaults.

        Precedence: YAML values override defaults. Per-call overrides
        happen at the service/CLI level (not here).
        """
        if config_path is None:
            from applypilot.config import APP_DIR

            config_path = APP_DIR / "config.yaml"

        if not config_path.exists():
            return cls()

        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            log.warning("Failed to parse %s: %s — using defaults", config_path, e)
            return cls()

        return cls(
            scoring=_merge(ScoringConfig, raw.get("scoring", {})),
            tailoring=_merge(TailoringConfig, raw.get("tailoring", {})),
            apply=_merge(ApplyConfig, raw.get("apply", {})),
            pipeline=_merge(PipelineConfig, raw.get("pipeline", {})),
        )


def _merge(cls, overrides: dict):
    """Create a frozen dataclass, applying only valid field overrides."""
    import dataclasses

    valid = {f.name for f in dataclasses.fields(cls)}
    filtered = {k: v for k, v in overrides.items() if k in valid}
    return cls(**filtered)
