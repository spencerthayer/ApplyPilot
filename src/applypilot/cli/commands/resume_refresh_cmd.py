"""Resume refresh — regenerate stale base resumes after profile edits (INIT-23)."""

from __future__ import annotations

import logging

import typer
from rich.console import Console

log = logging.getLogger(__name__)
console = Console()


def resume_refresh(
        force: bool = typer.Option(False, "--force", help="Regenerate all, not just stale."),
) -> None:
    """Force-regenerate stale base resumes after profile changes."""
    from applypilot.bootstrap import get_app
    from applypilot.config import APP_DIR, RESUME_JSON_PATH

    get_app()
    if not RESUME_JSON_PATH.exists():
        console.print("[red]No resume.json found. Run `applypilot init` first.[/red]")
        raise typer.Exit(1)

    resume_mtime = RESUME_JSON_PATH.stat().st_mtime

    # Cascade resume changes through piece system
    try:
        from applypilot.config import load_resume_json
        from applypilot.scoring.tailor.hybrid_bridge import refresh_pieces
        from applypilot.db.sqlite.connection import get_connection

        app = get_app()
        result = refresh_pieces(load_resume_json(), app.container.piece_repo, get_connection())
        if result["added"] or result["removed"]:
            console.print(
                f"[cyan]Pieces updated:[/cyan] {result['added']} added, {result['removed']} removed, "
                f"{result['overlays_invalidated']} overlays invalidated"
            )
        else:
            console.print("[dim]Pieces unchanged.[/dim]")
    except Exception as e:
        log.debug("Piece refresh skipped: %s", e)

    tailored_dir = APP_DIR / "tailored"

    if not tailored_dir.exists():
        console.print("[dim]No tailored resumes found.[/dim]")
        return

    stale = []
    for f in tailored_dir.iterdir():
        if f.suffix in (".txt", ".json", ".html", ".pdf"):
            if force or f.stat().st_mtime < resume_mtime:
                stale.append(f)

    if not stale:
        console.print("[green]All tailored resumes are up to date.[/green]")
        return

    console.print(f"Found {len(stale)} stale tailored resumes.")

    job_repo = get_app().container.job_repo
    reset_count = 0
    for f in stale:
        jobs = job_repo.find_by_tailored_path(str(f))
        for job in jobs:
            job_repo.clear_tailoring(job.url)
            reset_count += 1
        f.unlink()

    console.print(
        f"[yellow]Cleared {reset_count} jobs for re-tailoring. Run `applypilot run tailor` to regenerate.[/yellow]"
    )
