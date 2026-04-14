"""Analytics observer — background processing of analytics events.

Runs as a daemon thread, polling for unprocessed events and routing
them through aggregators. Never blocks the pipeline.
"""

from __future__ import annotations

import logging
import threading

from applypilot.analytics.aggregators import (
    CareerHealthReport,
    CareerRoadmapReport,
    EffectivenessReport,
    MarketIntelligenceReport,
    PoolSegmentationReport,
    SkillGapReport,
    TrackComparisonReport,
    generate_summary,
    process_event,
)
from applypilot.db.interfaces.analytics_repository import AnalyticsRepository

log = logging.getLogger(__name__)


class AnalyticsObserver:
    """Background observer that processes analytics events periodically."""

    def __init__(self, analytics_repo: AnalyticsRepository):
        self._repo = analytics_repo
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.skill_gaps = SkillGapReport()
        self.effectiveness = EffectivenessReport()
        self.pool = PoolSegmentationReport()
        self.market = MarketIntelligenceReport()
        self.health = CareerHealthReport()
        self.roadmap = CareerRoadmapReport()
        self.tracks = TrackComparisonReport()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="analytics-observer")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        # Create a thread-local repo to avoid SQLite threading issues
        from applypilot.db.sqlite.analytics_repo import SqliteAnalyticsRepository
        from applypilot.db.sqlite.connection import get_connection

        thread_conn = get_connection()
        self._thread_repo = SqliteAnalyticsRepository(thread_conn)
        while not self._stop.wait(timeout=10):
            self._process_batch()

    def _process_batch(self) -> None:
        repo = getattr(self, "_thread_repo", self._repo)
        events = repo.get_unprocessed(limit=200)
        for event in events:
            process_event(
                event.event_type,
                event.payload,
                skill_gaps=self.skill_gaps,
                effectiveness=self.effectiveness,
                pool=self.pool,
                market=self.market,
                health=self.health,
                roadmap=self.roadmap,
                tracks=self.tracks,
            )
            repo.mark_processed(event.event_id)

    def get_summary(self) -> dict:
        return generate_summary(
            self.skill_gaps,
            self.effectiveness,
            self.pool,
            market=self.market,
            health=self.health,
            roadmap=self.roadmap,
            tracks=self.tracks,
        )
