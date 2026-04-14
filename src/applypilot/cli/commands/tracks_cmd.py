"""CLI command: tracks — manage career tracks (P2)."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

console = Console()


def list_tracks() -> None:
    """List career tracks from DB (persisted during init)."""
    from applypilot.bootstrap import get_app

    tracks = get_app().container.track_repo.get_all_tracks()

    if not tracks:
        console.print("[dim]No tracks found. Run 'applypilot init' to discover tracks.[/dim]")
        return

    table = Table(title=f"Career Tracks ({len(tracks)})")
    table.add_column("ID", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Skills")
    table.add_column("Active")
    for t in tracks:
        skills = ", ".join(t["skills"][:8])
        if len(t["skills"]) > 8:
            skills += "..."
        table.add_row(
            t["track_id"][:8],
            t["name"],
            skills,
            "[green]✓[/green]" if t["active"] else "[red]✗[/red]",
        )
    console.print(table)


def discover() -> None:
    """Re-discover career tracks from current profile via LLM."""
    from applypilot.bootstrap import get_app
    from applypilot.config import RESUME_JSON_PATH
    from applypilot.resume_json import load_resume_json_from_path
    from applypilot.services.track_discovery import discover_tracks_llm

    data = load_resume_json_from_path(RESUME_JSON_PATH)
    console.print("[dim]Analyzing profile...[/dim]")
    tracks = discover_tracks_llm(data)

    if not tracks:
        console.print("[dim]No distinct tracks detected.[/dim]")
        return

    app = get_app()
    track_repo = app.container.track_repo

    # Clear old tracks before saving new ones
    for old in track_repo.get_all_tracks():
        track_repo.delete_track(old["track_id"])

    for t in tracks:
        track_repo.save(t.track_id, t.name, t.skills, t.active)

    console.print(f"[green]Discovered and saved {len(tracks)} track(s):[/green]")
    for t in tracks:
        color = {"strong": "green", "moderate": "yellow", "weak": "red"}.get(t.data_strength, "dim")
        console.print(f"  [{color}]{t.data_strength}[/{color}] [bold]{t.name}[/bold]: {', '.join(t.skills[:6])}")
        if t.gaps:
            console.print(f"    [dim]Gaps: {', '.join(t.gaps[:3])}[/dim]")

    # Generate base resumes
    _generate_base_resumes(data, tracks)


def _generate_base_resumes(data, tracks):
    from applypilot.services.track_resumes import generate_track_base_resumes

    active = [t for t in tracks if t.active]
    if active:
        console.print("\n[dim]Generating per-track base resumes...[/dim]")
        generate_track_base_resumes(data, active)
