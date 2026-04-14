"""Reusable Rich panel formatting for CLI output."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

console = Console()


def info_panel(title: str, content: str) -> Panel:
    """Build an info panel with a title."""
    return Panel(content, title=f"[bold]{title}[/bold]", border_style="blue")


def error_panel(title: str, content: str) -> Panel:
    """Build an error panel with a title."""
    return Panel(content, title=f"[bold red]{title}[/bold red]", border_style="red")


def success_panel(title: str, content: str) -> Panel:
    """Build a success panel with a title."""
    return Panel(content, title=f"[bold green]{title}[/bold green]", border_style="green")


def stage_panel(stage: str, description: str) -> Panel:
    """Build a pipeline stage header panel."""
    return Panel(
        f"[dim]{description}[/dim]",
        title=f"[bold cyan]Stage: {stage}[/bold cyan]",
        border_style="cyan",
    )
