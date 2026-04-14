"""PieceRepository ABC — contract for atomic resume piece persistence."""

from abc import ABC, abstractmethod

from applypilot.db.dto import PieceDTO


class PieceRepository(ABC):
    @abstractmethod
    def save(self, piece: PieceDTO) -> None: ...

    @abstractmethod
    def save_many(self, pieces: list[PieceDTO]) -> None: ...

    @abstractmethod
    def get_by_id(self, piece_id: str) -> PieceDTO | None: ...

    @abstractmethod
    def get_by_hash(self, content_hash: str) -> PieceDTO | None: ...

    @abstractmethod
    def get_by_type(self, piece_type: str) -> list[PieceDTO]: ...

    @abstractmethod
    def get_children(self, parent_id: str) -> list[PieceDTO]: ...

    @abstractmethod
    def get_track_pieces(self, track_id: str) -> list[PieceDTO]: ...
