"""AnalyticsService — wraps analytics aggregation with DI."""

from __future__ import annotations

import logging

from applypilot.db.interfaces.analytics_repository import AnalyticsRepository
from applypilot.services.base import ServiceResult

log = logging.getLogger(__name__)


class AnalyticsService:
    def __init__(self, analytics_repo: AnalyticsRepository):
        self._analytics_repo = analytics_repo

    def get_unprocessed(self, limit: int = 100) -> ServiceResult:
        events = self._analytics_repo.get_unprocessed(limit=limit)
        return ServiceResult(data={"events": events, "count": len(events)})

    def get_by_type(self, event_type: str, limit: int = 100) -> ServiceResult:
        events = self._analytics_repo.get_by_type(event_type, limit=limit)
        return ServiceResult(data={"events": events, "count": len(events)})
