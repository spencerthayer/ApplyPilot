"""CLI command: applypilot timeline <URL>"""

from __future__ import annotations

import json
from typing import Optional

import typer
import applypilot.cli as _cli


def timeline(
    url: str = typer.Argument(..., help="Job URL to inspect"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show the full lifecycle timeline for a job URL."""
    _cli._bootstrap()

    from applypilot.analytics.timeline import get_job_timeline, format_timeline

    data = get_job_timeline(url)
    if not data:
        typer.echo(f"Job not found: {url[:80]}")
        raise typer.Exit(1)

    if as_json:
        typer.echo(json.dumps(data, indent=2, default=str))
    else:
        typer.echo(format_timeline(data))
