"""Recover command — scan for stale state and reset (APPLY-22)."""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console

log = logging.getLogger(__name__)
console = Console()


def recover(
        timeout: int = typer.Option(5, help="Minutes before in-progress jobs are considered stale."),
        clean_artifacts: bool = typer.Option(False, "--clean", help="Remove partial artifact files."),
) -> None:
    """Scan for stale state and reset — stuck jobs, partial artifacts, orphan locks."""
    from applypilot.bootstrap import get_app
    from applypilot.config import TAILORED_DIR, COVER_LETTER_DIR

    app = get_app()
    job_repo = app.container.job_repo

    # 1. Reset stale in-progress jobs
    stale = job_repo.reset_stale_in_progress(timeout_minutes=timeout)
    console.print(f"  Reset {stale} stale in-progress jobs (>{timeout}min)")

    # 2. Clean partial artifacts if requested
    cleaned = 0
    if clean_artifacts:
        for d in [TAILORED_DIR, COVER_LETTER_DIR]:
            if not d or not Path(d).exists():
                continue
            for f in Path(d).glob("*.partial"):
                f.unlink()
                cleaned += 1
        console.print(f"  Cleaned {cleaned} partial artifact files")

    total = stale + cleaned
    if total == 0:
        console.print("[green]No stale state found — everything looks clean.[/green]")
    else:
        console.print(f"[yellow]Recovered {total} items.[/yellow]")
