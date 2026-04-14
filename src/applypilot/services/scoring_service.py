"""ScoringService — wraps scoring/scorer.py with DI."""

from __future__ import annotations

import logging

from applypilot.db.interfaces.job_repository import JobRepository
from applypilot.db.interfaces.llm_cache_repository import LLMCacheRepository
from applypilot.services.base import ServiceResult

log = logging.getLogger(__name__)


class ScoringService:
    def __init__(self, job_repo: JobRepository, llm_cache_repo: LLMCacheRepository):
        self._job_repo = job_repo
        self._llm_cache_repo = llm_cache_repo

    def score_jobs(self, *, job_url: str | None = None) -> ServiceResult:
        """Delegate to existing run_scoring — it handles its own DB access for now."""
        try:
            from applypilot.scoring.scorer import run_scoring

            run_scoring(job_url=job_url)
            return ServiceResult(data={"action": "scoring_complete"})
        except Exception as e:
            log.exception("Scoring failed: %s", e)
            return ServiceResult(success=False, error=str(e))
