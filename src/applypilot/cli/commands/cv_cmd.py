"""CV command — generate comprehensive CV from Master Profile (INIT-20)."""

from __future__ import annotations

import logging

import typer
from rich.console import Console

log = logging.getLogger(__name__)
console = Console()


def cv_render(
        format: str = typer.Option("html", help="Output format: html or pdf."),
        output: str = typer.Option("", help="Output file path (auto-generated if empty)."),
        theme: str = typer.Option("", help="JSON Resume theme to use."),
) -> None:
    """Render comprehensive CV from Master Profile — all sections, no page limit."""
    from pathlib import Path

    from applypilot.bootstrap import get_app
    from applypilot.config import APP_DIR, RESUME_JSON_PATH
    from applypilot.resume_render import render_resume_html, render_resume_pdf

    get_app()

    if not RESUME_JSON_PATH.exists():
        console.print("[red]No resume.json found. Run `applypilot init` first.[/red]")
        raise typer.Exit(1)

    out_path = Path(output) if output else APP_DIR / f"cv.{format}"
    theme_arg = theme or None

    if format == "html":
        rendered, used_theme = render_resume_html(
            resume_path=RESUME_JSON_PATH,
            theme=theme_arg,
            output_path=out_path,
        )
        console.print(f"[green]CV rendered to {rendered} (theme: {used_theme})[/green]")
    elif format == "pdf":
        rendered, used_theme = render_resume_pdf(
            resume_path=RESUME_JSON_PATH,
            theme=theme_arg,
            output_path=out_path,
        )
        console.print(f"[green]CV rendered to {rendered} (theme: {used_theme})[/green]")
    else:
        console.print(f"[red]Unsupported format: {format}. Use html or pdf.[/red]")
        raise typer.Exit(1)
