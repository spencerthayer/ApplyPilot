"""TrackRepository ABC — contract for track-to-piece mapping persistence."""

from abc import ABC, abstractmethod

from applypilot.db.dto import TrackMappingDTO


class TrackRepository(ABC):
    @abstractmethod
    def save_mapping(self, mapping: TrackMappingDTO) -> None: ...

    @abstractmethod
    def get_mappings(self, track_id: str) -> list[TrackMappingDTO]: ...

    @abstractmethod
    def delete_track(self, track_id: str) -> int: ...

    @abstractmethod
    def save(self, track_id: str, name: str, skills: list[str], active: bool) -> None: ...

    @abstractmethod
    def get_all_tracks(self) -> list[dict]: ...

    @abstractmethod
    def set_active(self, track_id: str, active: bool) -> None: ...

    @abstractmethod
    def update_base_resume_path(self, track_id: str, path: str) -> None: ...
