"""Base tier handler ABC and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod

from applypilot.apply.classifier.models import ClassificationResult, RedirectChain
from applypilot.db.dto import ApplyResultDTO, JobDTO


class TierHandler(ABC):
    """Base class for all tier-specific apply handlers."""

    @abstractmethod
    def handle(
            self,
            job: JobDTO,
            chain: RedirectChain,
            classification: ClassificationResult,
            resume_path: str,
            profile: dict,
    ) -> ApplyResultDTO:
        """Execute the apply strategy for this tier.

        Returns ApplyResultDTO with status: applied|failed|needs_human.
        """
        ...
