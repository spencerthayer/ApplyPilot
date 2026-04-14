"""CLI command: config."""

from __future__ import annotations

import typer

import applypilot.cli as _cli

console = _cli.console

__all__ = ["config_show", "config_init"]


def config_show() -> None:
    """Show current runtime configuration (defaults + config.yaml overrides)."""
    _cli._bootstrap()

    from applypilot.bootstrap import get_app
    import dataclasses

    rc = get_app().config
    console.print("\n[bold]ApplyPilot Runtime Configuration[/bold]\n")

    for section_name in ("scoring", "tailoring", "apply", "pipeline"):
        section = getattr(rc, section_name)
        console.print(f"[cyan]{section_name}:[/cyan]")
        for f in dataclasses.fields(section):
            val = getattr(section, f.name)
            console.print(f"  {f.name}: {val}")
        console.print()

    from applypilot.config import APP_DIR

    yaml_path = APP_DIR / "config.yaml"
    if yaml_path.exists():
        console.print(f"[dim]Loaded from: {yaml_path}[/dim]")
    else:
        console.print("[dim]No config.yaml found — using defaults.[/dim]")
        console.print(f"[dim]Create one at: {yaml_path}[/dim]")
    console.print()


def config_init() -> None:
    """Create a config.yaml with current defaults."""
    _cli._bootstrap()

    from applypilot.config import APP_DIR
    import shutil

    yaml_path = APP_DIR / "config.yaml"
    if yaml_path.exists():
        console.print(f"[yellow]Already exists:[/yellow] {yaml_path}")
        return

    from applypilot.config import CONFIG_DIR

    example = CONFIG_DIR / "config.example.yaml"
    if example.exists():
        shutil.copy(example, yaml_path)
        console.print(f"[green]Created:[/green] {yaml_path}")
    else:
        console.print("[red]Example config not found in package.[/red]")
        raise typer.Exit(code=1)


def config_set(
        key: str = typer.Argument(..., help="Config key in dot notation, e.g. scoring.min_score"),
        value: str = typer.Argument(..., help="Value to set"),
) -> None:
    """Set a config value in config.yaml."""
    _cli._bootstrap()

    from applypilot.config import APP_DIR
    import yaml

    yaml_path = APP_DIR / "config.yaml"
    if yaml_path.exists():
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    else:
        data = {}

    parts = key.split(".")
    if len(parts) != 2:
        console.print("[red]Key must be section.name, e.g. scoring.min_score[/red]")
        raise typer.Exit(code=1)

    section, name = parts

    # Auto-cast value
    try:
        cast_val = int(value)
    except ValueError:
        try:
            cast_val = float(value)
        except ValueError:
            cast_val = value

    data.setdefault(section, {})[name] = cast_val
    yaml_path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    console.print(f"[green]Set {key} = {cast_val}[/green] in {yaml_path}")
