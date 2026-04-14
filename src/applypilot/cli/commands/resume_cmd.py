"""CLI commands: render_resume, tailor_cmd."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

import applypilot.cli as _cli

console = _cli.console

__all__ = ["render_resume", "tailor_cmd", "pieces_cmd", "import_resume_cmd"]


def render_resume(
        theme: Optional[str] = typer.Option(None, "--theme", help="Theme package name, e.g. jsonresume-theme-even."),
        format: str = typer.Option("html", "--format", help="Output format: html or pdf."),
        output: Optional[Path] = typer.Option(None, "--output", help="Optional output path."),
        track: Optional[str] = typer.Option(None, "--track", help="Render track-specific base resume from DB pieces."),
        job_url: Optional[str] = typer.Option(None, "--job-url", help="Render per-job tailored resume from DB pieces."),
        from_file: bool = typer.Option(False, "--from-file",
                                       help="Force file-based themed render instead of DB pieces."),
) -> None:
    """Render the canonical resume. Default: from DB pieces. Use --from-file for themed render."""
    _cli._bootstrap()

    if format not in {"html", "pdf"}:
        console.print("[red]Invalid --format value.[/red] Choose 'html' or 'pdf'.")
        raise typer.Exit(code=1)

    # Default: render from DB pieces (unless --from-file or --theme forces file path)
    if not from_file and theme is None:
        try:
            from applypilot.resume_render import render_resume_from_db

            rendered_path = render_resume_from_db(
                output_path=output,
                track_id=track,
                job_url=job_url,
                fmt=format,
            )
            console.print(f"[green]Rendered {format.upper()} from DB pieces[/green] -> {rendered_path}")
            return
        except Exception as exc:
            console.print(f"[dim]DB render unavailable ({exc}), falling back to file.[/dim]")

    # Fallback: file-based themed render
    resume_json_path = getattr(_cli, "RESUME_JSON_PATH", None)
    if resume_json_path is None:
        from applypilot.config import RESUME_JSON_PATH

        resume_json_path = RESUME_JSON_PATH

    if not resume_json_path.exists():
        console.print(f"[red]Canonical resume not found:[/red] {resume_json_path}")
        raise typer.Exit(code=1)

    from applypilot.resume_render import render_resume_html, render_resume_pdf

    try:
        if format == "html":
            rendered_path, resolved_theme = render_resume_html(theme=theme, output_path=output)
        else:
            rendered_path, resolved_theme = render_resume_pdf(theme=theme, output_path=output)
    except Exception as exc:
        console.print(f"[red]Resume render failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]Rendered {format.upper()}[/green] using [bold]{resolved_theme}[/bold] -> {rendered_path}")


def tailor_cmd(
        url: Optional[str] = typer.Option(None, "--url", help="Tailor resume for a specific job URL."),
        min_score: int = typer.Option(7, "--min-score", help="Minimum fit score for tailoring."),
        limit: int = typer.Option(0, "--limit", "-l", help="Max jobs to process when --url is not provided."),
        force: bool = typer.Option(False, "--force", help="Regenerate even if a tailored resume already exists."),
        validation: str = typer.Option(
            "normal",
            "--validation",
            help=(
                    "Validation strictness: strict, normal, lenient. "
                    "Use normal unless you specifically want stricter/faster behavior."
            ),
        ),
) -> None:
    """Generate tailored resume artifacts."""
    _cli._bootstrap()

    from applypilot.bootstrap import get_app
    from applypilot.config import check_tier

    valid_modes = ("strict", "normal", "lenient")
    if validation not in valid_modes:
        console.print(f"[red]Invalid --validation value:[/red] '{validation}'. Choose from: {', '.join(valid_modes)}")
        raise typer.Exit(code=1)

    check_tier(2, "resume tailoring")

    result = get_app().resume_svc.run_tailoring(
        min_score=min_score,
        limit=limit,
        validation_mode=validation,
        target_url=url,
        force=force,
    )
    if not result.success:
        console.print(f"[red]Tailoring failed:[/red] {result.error}")
        raise typer.Exit(code=1)
    data = result.data or {}
    console.print(
        "[green]Tailoring finished:[/green] "
        f"{data.get('approved', 0)} approved, "
        f"{data.get('failed', 0)} failed, "
        f"{data.get('errors', 0)} errors."
    )


def pieces_cmd(
        piece_type: Optional[str] = typer.Option(
            None,
            "--type",
            "-t",
            help="Filter by type: header, summary, experience_entry, bullet, skill_group, education, project.",
        ),
) -> None:
    """Show decomposed resume pieces from the database."""
    _cli._bootstrap()

    from applypilot.bootstrap import get_app

    app = get_app()

    # Auto-decompose if no pieces exist yet
    result = app.resume_svc.get_pieces(piece_type="header")
    if result.success and result.data["count"] == 0:
        resume = app.profile.load_resume_json()
        if not resume:
            console.print("[red]No resume.json found.[/red] Run 'applypilot init' first.")
            raise typer.Exit(code=1)
        dec = app.resume_svc.decompose(resume)
        if dec.success:
            console.print(
                f"[green]Auto-decomposed:[/green] {dec.data['pieces']} pieces ({dec.data['bullets']} bullets)"
            )

    result = app.resume_svc.get_pieces(piece_type=piece_type)
    if not result.success:
        console.print(f"[red]{result.error}[/red]")
        raise typer.Exit(code=1)

    pieces = result.data["pieces"]
    if not pieces:
        console.print("[yellow]No pieces found.[/yellow]")
        return

    from rich.table import Table

    table = Table(title=f"Resume Pieces ({result.data['count']})")
    table.add_column("Type", style="cyan")
    table.add_column("Content", max_width=80)
    table.add_column("Hash", style="dim", max_width=10)

    for p in pieces:
        content = p.content[:77] + "..." if len(p.content) > 80 else p.content
        table.add_row(p.piece_type, content, p.content_hash[:8])

    console.print(table)


def import_resume_cmd(
        files: list[Path] = typer.Argument(..., help="PDF or TXT files to import."),
        output: Optional[Path] = typer.Option(None, "--output", help="Output path for resume.json."),
) -> None:
    """Import resume from PDF/TXT files into canonical resume.json."""
    _cli._bootstrap()

    from applypilot.resume_ingest import ingest_resumes
    from applypilot.config import RESUME_JSON_PATH

    out = output or RESUME_JSON_PATH
    try:
        ingest_resumes([str(f) for f in files], output_path=str(out))
        console.print(f"[green]Imported to:[/green] {out}")

        # Auto-decompose
        from applypilot.bootstrap import get_app
        import json

        resume = json.loads(out.read_text(encoding="utf-8"))
        result = get_app().resume_svc.decompose(resume)
        if result.success:
            console.print(f"[green]Decomposed:[/green] {result.data['pieces']} pieces")
    except Exception as e:
        console.print(f"[red]Import failed:[/red] {e}")
        raise typer.Exit(code=1)
