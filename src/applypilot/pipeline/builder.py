"""Pipeline builder — composable, fluent API for running pipeline stages."""

from __future__ import annotations

import logging
import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from applypilot.pipeline.context import PipelineContext
from applypilot.pipeline.stages import _app
from applypilot.pipeline.stage import Stage
from applypilot.pipeline.stages import STAGES

log = logging.getLogger(__name__)
console = Console()

VALID_STAGES = list(STAGES.keys())


class Pipeline:
    """Builder for composing pipeline stages. Supports batch, single-job, sequential, chunked, and streaming modes."""

    def __init__(self, ctx: PipelineContext | None = None) -> None:
        self._ctx = ctx or PipelineContext()
        self._stages: list[Stage] = []
        self._stream = False
        self._chunked = False
        self._chunk_size = 1000

    @classmethod
    def batch(
            cls,
            stages: list[str] | None = None,
            min_score: int = 7,
            workers: int = 1,
            validation_mode: str = "normal",
            dry_run: bool = False,
            stream: bool = False,
            chunked: bool = False,
            chunk_size: int = 1000,
            limit: int = 0,
            urls: list[str] | None = None,
            sources: list[str] | None = None,
            companies: list[str] | None = None,
            strict_title: bool = False,
            force: bool = False,
    ) -> Pipeline:
        """Batch mode: run named stages over all pending jobs."""
        ctx = PipelineContext(
            min_score=min_score,
            limit=limit,
            workers=workers,
            validation_mode=validation_mode,
            dry_run=dry_run,
            urls=urls,
            sources=sources,
            companies=companies,
            strict_title=strict_title,
            force=force,
        )
        p = cls(ctx)
        p._stream = stream
        p._chunked = chunked or stream  # streaming implies chunked
        p._chunk_size = chunk_size
        for name in _resolve(stages):
            p._stages.append(STAGES[name])
        return p

    @classmethod
    def for_job(cls, url: str, min_score: int = 0, validation_mode: str = "normal") -> Pipeline:
        """Single-job mode: run stages scoped to one URL."""
        ctx = PipelineContext(min_score=min_score, limit=1, urls=[url], validation_mode=validation_mode)
        return cls(ctx)

    def discover(self) -> Pipeline:
        self._stages.append(STAGES["discover"])
        return self

    def enrich(self) -> Pipeline:
        self._stages.append(STAGES["enrich"])
        return self

    def score(self) -> Pipeline:
        self._stages.append(STAGES["score"])
        return self

    def tailor(self) -> Pipeline:
        self._stages.append(STAGES["tailor"])
        return self

    def cover(self) -> Pipeline:
        self._stages.append(STAGES["cover"])
        return self

    def pdf(self) -> Pipeline:
        self._stages.append(STAGES["pdf"])
        return self

    def execute(self) -> dict:
        """Run all composed stages sequentially (or chunked if enabled)."""
        # Chunked mode only makes sense when discover/enrich/score are in the stage list.
        chunked_stages = {"discover", "enrich", "score"}
        has_chunked_stages = any(s.name in chunked_stages for s in self._stages)

        # Auto-enable chunked when running full pipeline (discover+enrich+score)
        all_three = all(any(s.name == n for s in self._stages) for n in ("discover", "enrich", "score"))
        auto_chunked = self._chunked or (all_three and not self._ctx.is_single)

        # Never chunk when URLs are set — scoped runs must be sequential
        if self._ctx.urls:
            auto_chunked = False

        if auto_chunked and has_chunked_stages:
            return self._execute_chunked()
        return self._execute_sequential()

    def _execute_chunked(self) -> dict:
        """Run discover→enrich→score in overlapping chunks via producer-consumer threads."""
        from applypilot.pipeline.chunked import ChunkedExecutor

        console.print(Panel(f"[bold]ApplyPilot Pipeline (chunked, {self._chunk_size}/chunk)[/bold]"))
        stage_names = [s.name for s in self._stages]
        console.print(f"  Stages:     {' → '.join(stage_names)}\n")

        def discover_fn(ctx):
            for s in self._stages:
                if s.name == "discover":
                    s.run(ctx)
            # After discovery, enrichment starts immediately on all discovered jobs
            # The chunked executor handles the overlap between enrich and score
            return _app().container.job_repo.get_pipeline_counts().get("total", 0)

        def enrich_fn(chunk_idx):
            for s in self._stages:
                if s.name == "enrich":
                    s.run(self._ctx)

        def score_fn():
            for s in self._stages:
                if s.name == "score":
                    s.run(self._ctx)

        executor = ChunkedExecutor(self._ctx, chunk_size=self._chunk_size)
        result = executor.execute(discover_fn, enrich_fn, score_fn)

        # Run remaining stages (tailor, cover, pdf) sequentially after chunked stages
        for stage in self._stages:
            if stage.name not in ("discover", "enrich", "score"):
                console.print(f"  STAGE: {stage.name}")
                stage.run(self._ctx)

        console.print(f"\n  Chunked pipeline: {result['chunks']} chunks in {result['elapsed']:.1f}s")
        if result["errors"]:
            console.print(f"  Errors: {result['errors']}")
        return result

    def _execute_sequential(self) -> dict:
        """Run all composed stages sequentially and return summary."""
        mode = "single" if self._ctx.is_single else "batch"
        stage_names = [s.name for s in self._stages]

        console.print(Panel(f"[bold]ApplyPilot Pipeline ({mode})[/bold]"))
        console.print(f"  Stages:     {' → '.join(stage_names)}")
        if self._ctx.is_single:
            console.print(f"  Job:        {self._ctx.job_url}")

        from applypilot.bootstrap import get_app

        _repo = get_app().container.job_repo
        status_counts = _repo.count_by_status()
        total_jobs = sum(status_counts.values())
        counts = _repo.get_pipeline_counts()
        pending = counts["total"] - counts["with_desc"]
        console.print(f"  DB:         {total_jobs} jobs, {pending} pending enrichment\n")

        results: list[dict] = []
        total_start = time.time()

        for stage in self._stages:
            console.print("=" * 70)
            console.print(f"  STAGE: {stage.name} — {stage.description}")
            console.print(f"  Started: {time.strftime('%H:%M:%S')}")
            console.print("=" * 70)

            result = stage.run(self._ctx)
            results.append({"stage": stage.name, "status": result.status, "elapsed": result.elapsed})
            console.print(f"\n  Stage '{stage.name}' completed in {result.elapsed:.1f}s — {result.status}\n")

        total_elapsed = time.time() - total_start
        _print_summary(results, total_elapsed)

        from applypilot.bootstrap import get_app

        counts = get_app().container.job_repo.get_pipeline_counts()
        console.print("\n  DB Final State:")
        for label, key in [
            ("Total jobs", "total"),
            ("With desc", "with_desc"),
            ("Scored", "scored"),
            ("Tailored", "tailored"),
            ("Cover letters", "cover_letters"),
            ("Ready to apply", "ready_to_apply"),
            ("Applied", "applied"),
        ]:
            console.print(f"    {label:15s} {counts[key]}")
        console.print("=" * 70)

        errors = [r for r in results if r["status"].startswith("error")]
        return {"stages": results, "elapsed": total_elapsed, "errors": errors}


def _resolve(stage_names: list[str] | None) -> list[str]:
    names = stage_names or ["all"]
    if "all" in names:
        return VALID_STAGES
    return [n for n in names if n in VALID_STAGES]


def _print_summary(results: list[dict], total: float) -> None:
    table = Table(title="Pipeline Summary")
    table.add_column("Stage")
    table.add_column("Status")
    table.add_column("Time", justify="right")
    for r in results:
        table.add_row(r["stage"], r["status"], f"{r['elapsed']:.1f}s")
    table.add_row("", "", "")
    table.add_row("Total", "", f"{total:.1f}s")
    console.print(table)
