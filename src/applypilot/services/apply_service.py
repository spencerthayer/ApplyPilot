"""ApplyService — wraps apply/launcher.py with DI."""

from __future__ import annotations

import logging

from applypilot.db.interfaces.analytics_repository import AnalyticsRepository
from applypilot.db.interfaces.job_repository import JobRepository
from applypilot.services.base import ServiceResult

log = logging.getLogger(__name__)


class ApplyService:
    def __init__(self, job_repo: JobRepository, analytics_repo: AnalyticsRepository):
        self._job_repo = job_repo
        self._analytics_repo = analytics_repo

    def run_apply(self, **kwargs) -> ServiceResult:
        """Delegate to existing apply main."""
        try:
            from applypilot.apply.launcher import main as apply_main

            apply_main(**kwargs)
            return ServiceResult(data={"action": "apply_complete"})
        except Exception as e:
            log.exception("Apply failed: %s", e)
            return ServiceResult(success=False, error=str(e))

    def mark_job(self, url: str, status: str, *, reason: str | None = None) -> ServiceResult:
        from applypilot.apply.launcher import mark_job

        try:
            mark_job(url, status, reason=reason)
            return ServiceResult(data={"url": url, "status": status})
        except Exception as e:
            return ServiceResult(success=False, error=str(e))

    def reset_failed(self) -> ServiceResult:
        from applypilot.apply.launcher import reset_failed as do_reset

        count = do_reset()
        return ServiceResult(data={"reset_count": count})

    def gen_prompt(self, url: str, *, min_score: int = 7, model: str | None = None) -> ServiceResult:
        from applypilot.apply.launcher import gen_prompt

        prompt_file = gen_prompt(url, min_score=min_score, model=model)
        if not prompt_file:
            return ServiceResult(success=False, error="No matching job found for that URL.")
        return ServiceResult(data={"prompt_file": str(prompt_file)})
