"""Analytics package — event emission, aggregation, and background observer."""

from applypilot.analytics.events import emit
from applypilot.analytics.observer import AnalyticsObserver

__all__ = ["emit", "AnalyticsObserver"]
