"""CLI command: reset — data deletion (LLD §17.8)."""

from __future__ import annotations

import shutil
from pathlib import Path

import typer

import applypilot.cli as _cli

console = _cli.console

__all__ = ["reset"]


def reset(
        all_data: bool = typer.Option(False, "--all", help="Delete DB + config + tailored artifacts."),
        db: bool = typer.Option(False, "--db", help="Delete database only."),
        artifacts: bool = typer.Option(False, "--artifacts", help="Delete tailored resumes + cover letters only."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """Wipe user data. Use --all for full reset, --db for database only."""
    from applypilot.config import APP_DIR, TAILORED_DIR

    if not any([all_data, db, artifacts]):
        console.print("[red]Specify --all, --db, or --artifacts.[/red]")
        raise typer.Exit(code=1)

    targets: list[tuple[str, Path]] = []
    if all_data or db:
        db_path = APP_DIR / "applypilot.db"
        if db_path.exists():
            targets.append(("Database", db_path))
    if all_data or artifacts:
        if TAILORED_DIR.exists():
            targets.append(("Tailored artifacts", TAILORED_DIR))
    if all_data:
        for name in ("profile.json", "resume.json", "resume.txt", "searches.yaml", ".env"):
            p = APP_DIR / name
            if p.exists():
                targets.append((name, p))

    if not targets:
        console.print("[yellow]Nothing to delete.[/yellow]")
        return

    console.print("[bold red]Will delete:[/bold red]")
    for label, path in targets:
        console.print(f"  {label}: {path}")

    if not yes:
        confirm = typer.confirm("Proceed?")
        if not confirm:
            console.print("Cancelled.")
            raise typer.Exit()

    for label, path in targets:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        console.print(f"  [green]Deleted:[/green] {label}")

    console.print("[green]Reset complete.[/green]")
