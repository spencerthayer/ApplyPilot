"""Analytics event emission — non-blocking write via repository interface."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from applypilot.db.dto import AnalyticsEventDTO
from applypilot.db.interfaces.analytics_repository import AnalyticsRepository


def emit(
        stage: str,
        event_type: str,
        payload: str,
        analytics_repo: AnalyticsRepository,
) -> None:
    """Emit an analytics event via repository. Non-blocking."""
    event = AnalyticsEventDTO(
        event_id=uuid.uuid4().hex[:12],
        timestamp=datetime.now(timezone.utc).isoformat(),
        stage=stage,
        event_type=event_type,
        payload=payload,
    )
    analytics_repo.emit_event(event)
