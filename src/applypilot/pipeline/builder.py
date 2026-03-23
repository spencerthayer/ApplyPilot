"""Pipeline builder — composable, fluent API for running pipeline stages."""

from __future__ import annotations

import logging
import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from applypilot.database import get_connection
from applypilot.pipeline.context import PipelineContext
from applypilot.pipeline.stage import Stage, StageResult
from applypilot.pipeline.stages import STAGES

log = logging.getLogger(__name__)
console = Console()

VALID_STAGES = list(STAGES.keys())


class Pipeline:
    """Builder for composing pipeline stages. Supports batch, single-job, sequential, and streaming modes."""

    def __init__(self, ctx: PipelineContext | None = None) -> None:
        self._ctx = ctx or PipelineContext()
        self._stages: list[Stage] = []
        self._stream = False

    @classmethod
    def batch(cls, stages: list[str] | None = None, min_score: int = 7,
              workers: int = 1, validation_mode: str = "normal",
              dry_run: bool = False, stream: bool = False,
              limit: int = 0) -> Pipeline:
        """Batch mode: run named stages over all pending jobs."""
        ctx = PipelineContext(min_score=min_score, limit=limit, workers=workers,
                              validation_mode=validation_mode, dry_run=dry_run)
        p = cls(ctx)
        p._stream = stream
        for name in _resolve(stages):
            p._stages.append(STAGES[name])
        return p

    @classmethod
    def for_job(cls, url: str, min_score: int = 0, validation_mode: str = "normal") -> Pipeline:
        """Single-job mode: run stages scoped to one URL."""
        ctx = PipelineContext(min_score=min_score, limit=1, job_url=url, validation_mode=validation_mode)
        return cls(ctx)

    def discover(self) -> Pipeline:
        self._stages.append(STAGES["discover"]); return self
    def enrich(self) -> Pipeline:
        self._stages.append(STAGES["enrich"]); return self
    def score(self) -> Pipeline:
        self._stages.append(STAGES["score"]); return self
    def tailor(self) -> Pipeline:
        self._stages.append(STAGES["tailor"]); return self
    def cover(self) -> Pipeline:
        self._stages.append(STAGES["cover"]); return self
    def pdf(self) -> Pipeline:
        self._stages.append(STAGES["pdf"]); return self

    def execute(self) -> dict:
        """Run all composed stages sequentially and return summary."""
        mode = "single" if self._ctx.is_single else "batch"
        stage_names = [s.name for s in self._stages]

        console.print(Panel(f"[bold]ApplyPilot Pipeline ({mode})[/bold]"))
        console.print(f"  Stages:     {' → '.join(stage_names)}")
        if self._ctx.is_single:
            console.print(f"  Job:        {self._ctx.job_url}")
        conn = get_connection()
        total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE full_description IS NULL").fetchone()[0]
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

        conn = get_connection()
        console.print(f"\n  DB Final State:")
        for label, query in [
            ("Total jobs", "SELECT COUNT(*) FROM jobs"),
            ("With desc", "SELECT COUNT(*) FROM jobs WHERE full_description IS NOT NULL"),
            ("Scored", "SELECT COUNT(*) FROM jobs WHERE fit_score IS NOT NULL"),
            ("Tailored", "SELECT COUNT(*) FROM jobs WHERE tailored_resume_path IS NOT NULL"),
            ("Cover letters", "SELECT COUNT(*) FROM jobs WHERE cover_letter_path IS NOT NULL"),
            ("Ready to apply", "SELECT COUNT(*) FROM jobs WHERE tailored_resume_path IS NOT NULL AND applied_at IS NULL AND application_url IS NOT NULL"),
            ("Applied", "SELECT COUNT(*) FROM jobs WHERE applied_at IS NOT NULL"),
        ]:
            count = conn.execute(query).fetchone()[0]
            console.print(f"    {label:15s} {count}")
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
