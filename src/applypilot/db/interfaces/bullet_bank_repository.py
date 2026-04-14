"""BulletBankRepository ABC — contract for bullet bank persistence."""

from abc import ABC, abstractmethod

from applypilot.db.dto import BulletBankDTO, BulletFeedbackDTO


class BulletBankRepository(ABC):
    @abstractmethod
    def add_bullet(self, bullet: BulletBankDTO) -> None: ...

    @abstractmethod
    def get_bullet(self, bullet_id: str) -> BulletBankDTO | None: ...

    @abstractmethod
    def get_all(self, order_by_success: bool = True) -> list[BulletBankDTO]: ...

    @abstractmethod
    def record_feedback(self, feedback: BulletFeedbackDTO) -> None: ...

    @abstractmethod
    def update_stats(self, bullet_id: str) -> None: ...
