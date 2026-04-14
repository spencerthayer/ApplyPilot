"""AnalyticsRepository ABC — contract for analytics event persistence."""

from abc import ABC, abstractmethod

from applypilot.db.dto import AnalyticsEventDTO


class AnalyticsRepository(ABC):
    @abstractmethod
    def emit_event(self, event: AnalyticsEventDTO) -> None: ...

    @abstractmethod
    def get_unprocessed(self, limit: int = 100) -> list[AnalyticsEventDTO]: ...

    @abstractmethod
    def mark_processed(self, event_id: str) -> None: ...

    @abstractmethod
    def get_by_type(self, event_type: str, limit: int = 100) -> list[AnalyticsEventDTO]: ...
