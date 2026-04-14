"""OverlayRepository ABC — contract for per-job overlay persistence."""

from abc import ABC, abstractmethod

from applypilot.db.dto import OverlayDTO


class OverlayRepository(ABC):
    @abstractmethod
    def save(self, overlay: OverlayDTO) -> None: ...

    @abstractmethod
    def get_for_job(self, job_url: str, track_id: str | None = None) -> list[OverlayDTO]: ...

    @abstractmethod
    def get_for_piece(self, piece_id: str) -> list[OverlayDTO]: ...
