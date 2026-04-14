"""Reusable Rich table formatting for CLI output."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

console = Console()


def stats_table(title: str, rows: list[tuple[str, str | int]]) -> Table:
    """Build a two-column key-value stats table."""
    table = Table(title=title, show_header=False, padding=(0, 2))
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    for label, value in rows:
        table.add_row(label, str(value))
    return table


def score_distribution_table(distribution: list[tuple[int, int]]) -> Table:
    """Build a score distribution table (score → count)."""
    table = Table(title="Score Distribution")
    table.add_column("Score", justify="center", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Bar")
    max_count = max((c for _, c in distribution), default=1)
    for score, count in distribution:
        bar_len = int((count / max_count) * 30) if max_count else 0
        color = "green" if score >= 8 else "yellow" if score >= 6 else "red"
        table.add_row(str(score), str(count), f"[{color}]{'█' * bar_len}[/{color}]")
    return table


def jobs_table(title: str, jobs: list[dict], columns: list[str] | None = None) -> Table:
    """Build a table of jobs with configurable columns."""
    cols = columns or ["title", "site", "fit_score", "apply_status"]
    table = Table(title=title)
    for col in cols:
        justify = "right" if col in ("fit_score", "apply_attempts") else "left"
        table.add_column(col.replace("_", " ").title(), justify=justify)
    for job in jobs:
        table.add_row(*(str(job.get(c, "") or "")[:60] for c in cols))
    return table
