"""Bullet bank: repository-backed storage for resume bullet points and their variants."""

import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from applypilot.db.dto import BulletBankDTO, BulletFeedbackDTO
from applypilot.db.interfaces.bullet_bank_repository import BulletBankRepository
from applypilot.tailoring.models import Bullet


class BulletBank:
    """Persistent storage for resume bullets with usage tracking."""

    def __init__(self, repo: BulletBankRepository) -> None:
        self._repo = repo

    def add_bullet(
            self,
            text: str,
            context: Optional[dict] = None,
            tags: Optional[list] = None,
            metrics: Optional[list] = None,
    ) -> Bullet:
        now = datetime.now(timezone.utc).isoformat()
        dto = BulletBankDTO(
            id=str(uuid.uuid4()),
            text=text,
            context=json.dumps(context or {}),
            tags=json.dumps(tags or []),
            metrics=json.dumps(metrics or []),
            created_at=now,
        )
        self._repo.add_bullet(dto)
        return self._dto_to_bullet(dto)

    def get_bullet(self, bullet_id: str) -> Optional[Bullet]:
        dto = self._repo.get_bullet(bullet_id)
        return self._dto_to_bullet(dto) if dto else None

    def get_variants(self, tags: Optional[List[str]] = None) -> List[Bullet]:
        bullets = [self._dto_to_bullet(d) for d in self._repo.get_all()]
        if tags:
            bullets = [b for b in bullets if any(t in b.tags for t in tags)]
        return bullets

    def record_feedback(self, bullet_id: str, job_title: str, outcome: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._repo.record_feedback(
            BulletFeedbackDTO(
                bullet_id=bullet_id,
                job_title=job_title,
                outcome=outcome,
                created_at=now,
            )
        )
        self._repo.update_stats(bullet_id)

    @staticmethod
    def _dto_to_bullet(dto: BulletBankDTO) -> Bullet:
        return Bullet(
            id=dto.id,
            text=dto.text,
            context=json.loads(dto.context) if dto.context else {},
            tags=json.loads(dto.tags) if dto.tags else [],
            metrics=json.loads(dto.metrics) if dto.metrics else [],
            created_at=datetime.fromisoformat(dto.created_at) if dto.created_at else datetime.now(timezone.utc),
            use_count=dto.use_count,
            success_rate=dto.success_rate,
        )
