"""Models."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class SkillGapReport:
    """Most frequently missing skills across all scored jobs."""

    missing_skills: Counter = field(default_factory=Counter)
    total_jobs_analyzed: int = 0

    def ingest(self, payload: dict) -> None:
        if missing := payload.get("missing_requirements"):
            for skill in missing:
                self.missing_skills[skill.lower().strip()] += 1
            self.total_jobs_analyzed += 1

    def top(self, n: int = 20) -> list[tuple[str, int]]:
        return self.missing_skills.most_common(n)


@dataclass
class EffectivenessReport:
    """Apply success rates by tier and site."""

    by_tier: dict[str, Counter] = field(default_factory=dict)
    by_site: dict[str, Counter] = field(default_factory=dict)

    def ingest(self, payload: dict) -> None:
        tier = payload.get("tier", "unknown")
        site = payload.get("site", "unknown")
        status = payload.get("status", "unknown")

        self.by_tier.setdefault(tier, Counter())[status] += 1
        self.by_site.setdefault(site, Counter())[status] += 1

    def success_rate(self, group: dict[str, Counter]) -> dict[str, float]:
        return {key: counts.get("applied", 0) / max(sum(counts.values()), 1) for key, counts in group.items()}


@dataclass
class PoolSegmentationReport:
    """Job pool composition by site, score band, and location."""

    by_site: Counter = field(default_factory=Counter)
    by_score_band: Counter = field(default_factory=Counter)
    by_location: Counter = field(default_factory=Counter)
    total: int = 0

    def ingest(self, payload: dict) -> None:
        self.by_site[payload.get("site", "unknown")] += 1
        score = payload.get("fit_score")
        match score:
            case s if s is not None and s >= 9:
                self.by_score_band["9-10 (strong)"] += 1
            case s if s is not None and s >= 7:
                self.by_score_band["7-8 (good)"] += 1
            case s if s is not None and s >= 5:
                self.by_score_band["5-6 (moderate)"] += 1
            case s if s is not None:
                self.by_score_band["1-4 (skip)"] += 1
            case _:
                self.by_score_band["unscored"] += 1
        self.by_location[(payload.get("location") or "unknown")[:30]] += 1
        self.total += 1


@dataclass
class MarketIntelligenceReport:
    """Market intelligence: required skills, salary ranges, trends (ANALYZE-03)."""

    required_skills: Counter = field(default_factory=Counter)
    salary_mentions: list[str] = field(default_factory=list)
    locations: Counter = field(default_factory=Counter)
    seniority_levels: Counter = field(default_factory=Counter)
    total_jobs: int = 0

    def ingest(self, payload: dict) -> None:
        self.total_jobs += 1
        for skill in payload.get("matched_skills", []):
            self.required_skills[skill.lower().strip()] += 1
        for skill in payload.get("missing_requirements", []):
            self.required_skills[skill.lower().strip()] += 1
        if salary := payload.get("salary"):
            self.salary_mentions.append(salary)
        if loc := payload.get("location"):
            self.locations[loc[:30]] += 1
        if seniority := payload.get("seniority"):
            self.seniority_levels[seniority] += 1

    def top_skills(self, n: int = 20) -> list[tuple[str, int]]:
        return self.required_skills.most_common(n)

    def top_locations(self, n: int = 10) -> list[tuple[str, int]]:
        return self.locations.most_common(n)


@dataclass
class CareerHealthReport:
    """Career health score: composite per-track metric (ANALYZE-04).

    Formula: skill_coverage(40%) + experience_depth(25%) + app_success_rate(15%) + market_demand(20%)
    """

    matched_skill_counts: list[int] = field(default_factory=list)
    total_skill_counts: list[int] = field(default_factory=list)
    fit_scores: list[int] = field(default_factory=list)
    apply_outcomes: Counter = field(default_factory=Counter)

    def ingest_score(self, payload: dict) -> None:
        matched = len(payload.get("matched_skills", []))
        missing = len(payload.get("missing_requirements", []))
        total = matched + missing
        if total > 0:
            self.matched_skill_counts.append(matched)
            self.total_skill_counts.append(total)
        if score := payload.get("fit_score"):
            self.fit_scores.append(score)

    def ingest_apply(self, payload: dict) -> None:
        status = payload.get("status", "unknown")
        self.apply_outcomes[status] += 1

    def compute_score(self) -> float:
        """Compute 0-10 career health score."""
        # Skill coverage (40%)
        if self.total_skill_counts:
            avg_coverage = sum(m / t for m, t in zip(self.matched_skill_counts, self.total_skill_counts)) / len(
                self.total_skill_counts
            )
        else:
            avg_coverage = 0.0

        # Experience depth via avg fit score (25%)
        avg_fit = sum(self.fit_scores) / len(self.fit_scores) / 10.0 if self.fit_scores else 0.0

        # App success rate (15%)
        total_apps = sum(self.apply_outcomes.values())
        success_rate = self.apply_outcomes.get("applied", 0) / max(total_apps, 1)

        # Market demand — how many jobs scored 7+ (20%)
        high_fit = sum(1 for s in self.fit_scores if s >= 7)
        demand_ratio = high_fit / max(len(self.fit_scores), 1)

        raw = (avg_coverage * 0.40 + avg_fit * 0.25 + success_rate * 0.15 + demand_ratio * 0.20) * 10
        return round(min(max(raw, 0.0), 10.0), 1)


@dataclass
class CareerRoadmapReport:
    """Career roadmap: milestone-based improvement plan (ANALYZE-05).

    Generates prioritized skill acquisition recommendations based on
    gap frequency and market demand.
    """

    skill_gap_freq: Counter = field(default_factory=Counter)
    matched_skill_freq: Counter = field(default_factory=Counter)
    total_jobs: int = 0

    def ingest(self, payload: dict) -> None:
        self.total_jobs += 1
        for skill in payload.get("missing_requirements", []):
            self.skill_gap_freq[skill.lower().strip()] += 1
        for skill in payload.get("matched_skills", []):
            self.matched_skill_freq[skill.lower().strip()] += 1

    def generate_milestones(self, top_n: int = 5) -> list[dict]:
        """Generate prioritized skill milestones."""
        milestones = []
        for skill, freq in self.skill_gap_freq.most_common(top_n):
            pct = freq / max(self.total_jobs, 1) * 100
            milestones.append(
                {
                    "skill": skill,
                    "demand_frequency": freq,
                    "demand_pct": round(pct, 1),
                    "priority": "high" if pct >= 30 else "medium" if pct >= 15 else "low",
                }
            )
        return milestones

    def strengths(self, top_n: int = 5) -> list[tuple[str, int]]:
        return self.matched_skill_freq.most_common(top_n)


@dataclass
class TrackComparisonReport:
    """Track comparison: side-by-side track metrics (ANALYZE-07).

    Uses best_track_id from scored jobs when available, falls back to
    site-based segmentation.
    """

    segments: dict[str, Counter] = field(default_factory=dict)
    segment_scores: dict[str, list[int]] = field(default_factory=dict)

    def ingest(self, payload: dict) -> None:
        segment = payload.get("best_track_id") or payload.get("site", "unknown")
        self.segments.setdefault(segment, Counter())
        self.segments[segment]["total"] += 1
        if score := payload.get("fit_score"):
            self.segment_scores.setdefault(segment, []).append(score)
            if score >= 7:
                self.segments[segment]["high_fit"] += 1

    def compare(self) -> list[dict]:
        """Return segments sorted by average fit score."""
        result = []
        for seg, counts in self.segments.items():
            scores = self.segment_scores.get(seg, [])
            avg = sum(scores) / len(scores) if scores else 0
            result.append(
                {
                    "segment": seg,
                    "total_jobs": counts["total"],
                    "high_fit_jobs": counts.get("high_fit", 0),
                    "avg_score": round(avg, 1),
                }
            )
        return sorted(result, key=lambda x: x["avg_score"], reverse=True)


@dataclass
class TailoringReport:
    """Tailoring pipeline metrics: cost, cache hits, overlay reuse."""

    by_pipeline: Counter = field(default_factory=Counter)
    total_overlays: int = 0
    total_jobs: int = 0

    def ingest(self, payload: dict) -> None:
        self.by_pipeline[payload.get("pipeline", "unknown")] += 1
        self.total_overlays += payload.get("overlay_count", 0)
        self.total_jobs += 1

    def summary(self) -> dict:
        return {
            "total_jobs": self.total_jobs,
            "by_pipeline": dict(self.by_pipeline),
            "cache_hit_rate": round(self.by_pipeline.get("cache_hit", 0) / max(self.total_jobs, 1), 2),
            "total_overlays": self.total_overlays,
        }


@dataclass
class BulletEffectivenessReport:
    """Which bullet variants get interviews vs rejections."""

    bullet_outcomes: dict[str, Counter] = field(default_factory=dict)

    def ingest(self, bullet_id: str, outcome: str) -> None:
        self.bullet_outcomes.setdefault(bullet_id, Counter())[outcome] += 1

    def top_performers(self, n: int = 10) -> list[dict]:
        results = []
        for bid, counts in self.bullet_outcomes.items():
            total = sum(counts.values())
            wins = counts.get("applied", 0) + counts.get("interview", 0)
            results.append({"bullet_id": bid, "uses": total, "wins": wins, "rate": round(wins / max(total, 1), 2)})
        return sorted(results, key=lambda x: (-x["rate"], -x["uses"]))[:n]


@dataclass
class LatencyReport:
    """Latency percentiles for LLM calls and pipeline stages."""

    llm_latencies: list[float] = field(default_factory=list)
    stage_durations: dict[str, list[float]] = field(default_factory=dict)

    def ingest_llm(self, duration_ms: float) -> None:
        self.llm_latencies.append(duration_ms)

    def ingest_stage(self, stage: str, duration_ms: float) -> None:
        self.stage_durations.setdefault(stage, []).append(duration_ms)

    @staticmethod
    def _percentiles(values: list[float]) -> dict:
        if not values:
            return {}
        s = sorted(values)
        n = len(s)
        return {
            "count": n,
            "p50": s[n // 2],
            "p95": s[int(n * 0.95)],
            "p99": s[int(n * 0.99)],
            "mean": round(sum(s) / n, 1),
        }

    def summary(self) -> dict:
        result = {"llm": self._percentiles(self.llm_latencies)}
        for stage, durations in self.stage_durations.items():
            result[stage] = self._percentiles(durations)
        return result


@dataclass
class AlertReport:
    """Alerting thresholds — flag when metrics exceed bounds."""

    error_count: int = 0
    total_count: int = 0
    llm_failures: int = 0
    thresholds: dict = field(
        default_factory=lambda: {
            "error_rate": 0.10,  # alert if >10% errors
            "llm_failure_rate": 0.15,  # alert if >15% LLM failures
        }
    )

    def ingest_error(self) -> None:
        self.error_count += 1
        self.total_count += 1

    def ingest_success(self) -> None:
        self.total_count += 1

    def ingest_llm_failure(self) -> None:
        self.llm_failures += 1

    def check(self) -> list[str]:
        alerts = []
        if self.total_count > 10:
            rate = self.error_count / self.total_count
            if rate > self.thresholds["error_rate"]:
                alerts.append(f"Error rate {rate:.0%} exceeds {self.thresholds['error_rate']:.0%} threshold")
        if self.total_count > 5:
            llm_rate = self.llm_failures / self.total_count
            if llm_rate > self.thresholds["llm_failure_rate"]:
                alerts.append(
                    f"LLM failure rate {llm_rate:.0%} exceeds {self.thresholds['llm_failure_rate']:.0%} threshold"
                )
        return alerts
