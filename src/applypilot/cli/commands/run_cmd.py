"""CLI command: run."""

from __future__ import annotations

import logging
from typing import Optional

import typer

import applypilot.cli as _cli

console = _cli.console
VALID_STAGES = _cli.VALID_STAGES

__all__ = ["run"]


def run(
        stages: Optional[list[str]] = typer.Argument(
            None,
            help=(f"Pipeline stages to run. Valid: {', '.join(VALID_STAGES)}, all. Defaults to 'all' if omitted."),
        ),
        url: Optional[list[str]] = typer.Option(
            None,
            "--url",
            help="Job URLs — skip discover, run enrich→score→tailor→cover. Mutually exclusive with --source/--company.",
        ),
        source: Optional[str] = typer.Option(
            None,
            "--source",
            help="Comma-separated discovery sources to run: jobspy,workday,greenhouse,ashby,lever,smartextract,hackernews.",
        ),
        company: Optional[str] = typer.Option(
            None,
            "--company",
            help="Comma-separated company names or registry keys to filter discovery.",
        ),
        strict_title: bool = typer.Option(False, "--strict-title",
                                          help="Require ALL query terms in title (default: any)."),
        force: bool = typer.Option(False, "--force", help="Re-tailor already-tailored jobs."),
        min_score: int = typer.Option(7, "--min-score", help="Minimum fit score for tailor/cover stages."),
        limit: int = typer.Option(0, "--limit", "-l", help="Max jobs per tailor/cover batch (0 = all eligible)."),
        workers: int = typer.Option(1, "--workers", "-w", help="Parallel threads for discovery/enrichment stages."),
        stream: bool = typer.Option(False, "--stream", help="Run stages concurrently (streaming mode)."),
        chunked: bool = typer.Option(
            True, "--chunked/--no-chunk", help="Chunked mode: overlap discover→enrich→score (default: on)."
        ),
        chunk_size: int = typer.Option(1000, "--chunk-size", help="Jobs per chunk in chunked mode."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Preview stages without executing."),
        debug: bool = typer.Option(False, "--debug", "-d", help="Show detailed scoring output (keywords, reasoning)."),
        validation: str = typer.Option(
            "normal",
            "--validation",
            help=(
                    "Validation strictness for tailor/cover stages. "
                    "strict: banned words = errors, judge must pass. "
                    "normal: banned words = warnings only (default, recommended for Gemini free tier). "
                    "lenient: banned words ignored, LLM judge skipped (fastest, fewest API calls)."
            ),
        ),
) -> None:
    """Run pipeline stages: discover, enrich, score, tailor, cover, pdf."""
    _cli._bootstrap()

    if debug:
        logging.getLogger("applypilot").setLevel(logging.DEBUG)

    # Parse comma-separated flags
    sources = [s.strip() for s in source.split(",")] if source else None
    companies = [c.strip() for c in company.split(",")] if company else None
    urls = url  # typer handles list[str]

    # Mutual exclusion: --url conflicts with --source/--company
    if urls and (sources or companies):
        console.print("[red]--url is mutually exclusive with --source and --company[/red]")
        raise typer.Exit(code=1)

    # When --url set and no stages specified, default to enrich→cover (skip discover)
    if urls and not stages:
        stage_list = ["enrich", "score", "tailor", "cover"]
    else:
        stage_list = stages if stages else ["all"]

    # When --url set, add jobs to DB first
    if urls:
        from applypilot.bootstrap import get_app

        app = get_app()
        for u in urls:
            result = app.job_svc.add_single(u)
            if result.success:
                console.print(f"[green]Added:[/green] {result.data['title']}")
            else:
                console.print(f"[yellow]{result.error}[/yellow]")

    # Validate stage names
    for s in stage_list:
        if s != "all" and s not in VALID_STAGES:
            console.print(f"[red]Unknown stage:[/red] '{s}'. Valid stages: {', '.join(VALID_STAGES)}, all")
            raise typer.Exit(code=1)

    # Gate AI stages behind Tier 2
    llm_stages = {"score", "tailor", "cover"}
    if any(s in stage_list for s in llm_stages) or "all" in stage_list:
        from applypilot.config import check_tier

        check_tier(2, "AI scoring/tailoring")

    # Validate the --validation flag value
    valid_modes = ("strict", "normal", "lenient")
    if validation not in valid_modes:
        console.print(f"[red]Invalid --validation value:[/red] '{validation}'. Choose from: {', '.join(valid_modes)}")
        raise typer.Exit(code=1)

    from applypilot.pipeline import run_pipeline

    result = run_pipeline(
        stages=stage_list,
        min_score=min_score,
        limit=limit,
        dry_run=dry_run,
        stream=stream,
        chunked=chunked,
        chunk_size=chunk_size,
        workers=workers,
        validation_mode=validation,
        urls=urls,
        sources=sources,
        companies=companies,
        strict_title=strict_title,
        force=force,
    )

    if result.get("errors"):
        raise typer.Exit(code=1)
