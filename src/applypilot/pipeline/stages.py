"""Concrete pipeline stages — thin wrappers around existing modules."""

from __future__ import annotations

import logging
import time

from applypilot.pipeline.context import PipelineContext
from applypilot.pipeline.stage import StageResult

log = logging.getLogger(__name__)


class DiscoverStage:
    name = "discover"
    description = "Job discovery (JobSpy + Workday + smart extract + HN)"

    def run(self, ctx: PipelineContext) -> StageResult:
        if ctx.is_single:
            return StageResult(stage=self.name, status="skipped")
        t0 = time.time()
        stats: dict = {}
        for source, runner in _DISCOVERY_RUNNERS.items():
            try:
                runner(ctx)
                stats[source] = "ok"
            except Exception as e:
                log.error("%s failed: %s", source, e)
                stats[source] = f"error: {e}"
        errors = [k for k, v in stats.items() if isinstance(v, str) and v.startswith("error")]
        status = "error" if len(errors) == len(stats) else "partial" if errors else "ok"
        return StageResult(stage=self.name, status=status, elapsed=time.time() - t0, detail=stats)


class EnrichStage:
    name = "enrich"
    description = "Detail enrichment (full descriptions + apply URLs)"

    def run(self, ctx: PipelineContext) -> StageResult:
        t0 = time.time()
        try:
            from applypilot.enrichment.detail import run_enrichment
            run_enrichment(workers=ctx.workers, job_url=ctx.job_url)
            return StageResult(stage=self.name, elapsed=time.time() - t0)
        except Exception as e:
            log.error("Enrichment failed: %s", e)
            return StageResult(stage=self.name, status=f"error: {e}", elapsed=time.time() - t0)


class ScoreStage:
    name = "score"
    description = "LLM scoring (fit 1-10)"

    def run(self, ctx: PipelineContext) -> StageResult:
        t0 = time.time()
        try:
            from applypilot.scoring.scorer import run_scoring
            run_scoring(job_url=ctx.job_url)
            return StageResult(stage=self.name, elapsed=time.time() - t0)
        except Exception as e:
            log.exception("Scoring failed: %s", e)
            return StageResult(stage=self.name, status=f"error: {e}", elapsed=time.time() - t0)


class TailorStage:
    name = "tailor"
    description = "Resume tailoring (LLM + validation)"

    def run(self, ctx: PipelineContext) -> StageResult:
        t0 = time.time()
        try:
            from applypilot.scoring.tailor import run_tailoring
            run_tailoring(min_score=ctx.min_score, limit=ctx.limit,
                          validation_mode=ctx.validation_mode, target_url=ctx.job_url)
            return StageResult(stage=self.name, elapsed=time.time() - t0)
        except Exception as e:
            log.exception("Tailoring failed: %s", e)
            return StageResult(stage=self.name, status=f"error: {e}", elapsed=time.time() - t0)


class CoverStage:
    name = "cover"
    description = "Cover letter generation"

    def run(self, ctx: PipelineContext) -> StageResult:
        t0 = time.time()
        try:
            from applypilot.scoring.cover_letter import run_cover_letters
            kwargs = {"min_score": ctx.min_score, "limit": ctx.limit,
                      "validation_mode": ctx.validation_mode}
            # job_url only supported in single-job mode — upstream hasn't added it yet
            import inspect
            if "job_url" in inspect.signature(run_cover_letters).parameters:
                kwargs["job_url"] = ctx.job_url
            run_cover_letters(**kwargs)
            return StageResult(stage=self.name, elapsed=time.time() - t0)
        except Exception as e:
            log.exception("Cover letter failed: %s", e)
            return StageResult(stage=self.name, status=f"error: {e}", elapsed=time.time() - t0)


class PdfStage:
    name = "pdf"
    description = "PDF conversion (tailored resumes + cover letters)"

    def run(self, ctx: PipelineContext) -> StageResult:
        t0 = time.time()
        try:
            from applypilot.scoring.pdf import batch_convert
            batch_convert()
            return StageResult(stage=self.name, elapsed=time.time() - t0)
        except Exception as e:
            log.error("PDF conversion failed: %s", e)
            return StageResult(stage=self.name, status=f"error: {e}", elapsed=time.time() - t0)


STAGES: dict[str, object] = {
    "discover": DiscoverStage(),
    "enrich": EnrichStage(),
    "score": ScoreStage(),
    "tailor": TailorStage(),
    "cover": CoverStage(),
    "pdf": PdfStage(),
}


def _run_jobspy(ctx: PipelineContext) -> None:
    from applypilot.discovery.jobspy import run_discovery
    run_discovery(sites_override=ctx.sources)

def _run_workday(ctx: PipelineContext) -> None:
    from applypilot.discovery.workday import run_workday_discovery
    run_workday_discovery(workers=ctx.workers)

def _run_smartextract(ctx: PipelineContext) -> None:
    from applypilot.discovery.smartextract import run_smart_extract
    run_smart_extract(workers=ctx.workers)

def _run_hackernews(ctx: PipelineContext) -> None:
    from applypilot.discovery.hackernews import run_hn_discovery
    run_hn_discovery()

def _run_greenhouse(ctx: PipelineContext) -> None:
    from applypilot.discovery.greenhouse import search_all
    search_all("", workers=ctx.workers)

_DISCOVERY_RUNNERS: dict[str, object] = {
    "jobspy": _run_jobspy,
    "workday": _run_workday,
    "smartextract": _run_smartextract,
    "hackernews": _run_hackernews,
    "greenhouse": _run_greenhouse,
}
