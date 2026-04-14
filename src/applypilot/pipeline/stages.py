"""Concrete pipeline stages — wired through services via bootstrap.App.

Each stage delegates to the appropriate service method. Legacy modules
are still called under the hood (services wrap them), but the pipeline
no longer imports them directly.
"""

from __future__ import annotations

import logging
import time

from applypilot.pipeline.context import PipelineContext
from applypilot.pipeline.stage import StageResult

log = logging.getLogger(__name__)


def _app():
    """Lazy import to avoid circular deps at module load time."""
    from applypilot.bootstrap import get_app

    return get_app()


def _emit(stage: str, event_type: str, detail: dict | None = None) -> None:
    """Emit an analytics event for a pipeline stage transition."""
    try:
        import json
        from applypilot.analytics.events import emit

        app = _app()
        emit(stage, event_type, json.dumps(detail or {}), app.container.analytics_repo)
    except Exception:
        pass  # Analytics never blocks the pipeline


class DiscoverStage:
    name = "discover"
    description = "Job discovery (JobSpy + Workday + smart extract + HN)"

    def run(self, ctx: PipelineContext) -> StageResult:
        if ctx.urls:
            return StageResult(stage=self.name, status="skipped")
        _emit(self.name, "stage_started")
        t0 = time.time()
        result = _app().job_svc.run_discovery(
            workers=ctx.workers,
            sources=ctx.sources,
            companies=ctx.companies,
            strict_title=ctx.strict_title,
        )
        status = "ok" if result.success else "error"
        if result.data:
            errors = [k for k, v in result.data.items() if str(v).startswith("error")]
            if errors and len(errors) < len(result.data):
                status = "partial"
        _emit(self.name, "stage_completed", {"status": status, "elapsed": time.time() - t0})
        return StageResult(stage=self.name, status=status, elapsed=time.time() - t0, detail=result.data)


class EnrichStage:
    name = "enrich"
    description = "Detail enrichment (full descriptions + apply URLs)"

    def run(self, ctx: PipelineContext) -> StageResult:
        _emit(self.name, "stage_started")
        t0 = time.time()
        result = _app().job_svc.run_enrichment(workers=ctx.workers, job_url=ctx.job_url)
        status = "ok" if result.success else f"error: {result.error}"
        _emit(self.name, "stage_completed", {"status": status, "elapsed": time.time() - t0})
        return StageResult(stage=self.name, status=status, elapsed=time.time() - t0)


class ScoreStage:
    name = "score"
    description = "LLM scoring (fit 1-10)"

    def run(self, ctx: PipelineContext) -> StageResult:
        _emit(self.name, "stage_started")
        t0 = time.time()
        result = _app().scoring_svc.score_jobs(job_url=ctx.job_url)
        status = "ok" if result.success else f"error: {result.error}"
        _emit(self.name, "stage_completed", {"status": status, "elapsed": time.time() - t0})
        return StageResult(stage=self.name, status=status, elapsed=time.time() - t0)


class TailorStage:
    name = "tailor"
    description = "Resume tailoring (LLM + validation)"

    def run(self, ctx: PipelineContext) -> StageResult:
        _emit(self.name, "stage_started")
        t0 = time.time()
        result = _app().resume_svc.run_tailoring(
            min_score=ctx.min_score,
            limit=ctx.limit,
            validation_mode=ctx.validation_mode,
            target_url=ctx.job_url,
            force=ctx.force,
        )
        status = "ok" if result.success else f"error: {result.error}"
        _emit(self.name, "stage_completed", {"status": status, "elapsed": time.time() - t0})
        return StageResult(stage=self.name, status=status, elapsed=time.time() - t0)


class CoverStage:
    name = "cover"
    description = "Cover letter generation"

    def run(self, ctx: PipelineContext) -> StageResult:
        # Respect cover_letter.enabled from profile.json tailoring_config
        try:
            from applypilot.config import load_profile

            profile = load_profile()
            enabled = profile.get("tailoring_config", {}).get("cover_letter", {}).get("enabled", True)
            if not enabled:
                _emit(self.name, "stage_skipped", {"reason": "cover_letter.enabled=false"})
                log.info("Cover letter stage skipped (cover_letter.enabled=false in profile.json)")
                return StageResult(stage=self.name, status="skipped")
        except Exception:
            pass  # If profile can't be loaded, proceed normally

        _emit(self.name, "stage_started")
        t0 = time.time()
        result = _app().resume_svc.run_cover_letters(
            min_score=ctx.min_score,
            limit=ctx.limit,
            validation_mode=ctx.validation_mode,
            job_url=ctx.job_url,
        )
        status = "ok" if result.success else f"error: {result.error}"
        _emit(self.name, "stage_completed", {"status": status, "elapsed": time.time() - t0})
        return StageResult(stage=self.name, status=status, elapsed=time.time() - t0)


class PdfStage:
    name = "pdf"
    description = "PDF conversion (tailored resumes + cover letters)"

    def run(self, ctx: PipelineContext) -> StageResult:
        _emit(self.name, "stage_started")
        t0 = time.time()
        result = _app().resume_svc.run_pdf_conversion()
        status = "ok" if result.success else f"error: {result.error}"
        _emit(self.name, "stage_completed", {"status": status, "elapsed": time.time() - t0})
        return StageResult(stage=self.name, status=status, elapsed=time.time() - t0)


STAGES: dict[str, object] = {
    "discover": DiscoverStage(),
    "enrich": EnrichStage(),
    "score": ScoreStage(),
    "tailor": TailorStage(),
    "cover": CoverStage(),
    "pdf": PdfStage(),
}
