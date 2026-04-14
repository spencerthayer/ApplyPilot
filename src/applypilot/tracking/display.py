"""Display — extracted from tracking."""

from __future__ import annotations

import logging

from rich.console import Console
from rich.table import Table

from applypilot.tracking._compat import get_action_items

log = logging.getLogger(__name__)
console = Console()


def show_action_items() -> None:
    """Display pending action items as a Rich table."""
    items = get_action_items()
    if not items:
        console.print("[dim]No pending action items.[/dim]")
        return

    table = Table(title="Pending Action Items", show_header=True, header_style="bold cyan")
    table.add_column("Due", style="bold")
    table.add_column("Company")
    table.add_column("Title", max_width=25)
    table.add_column("Action", max_width=35)
    table.add_column("Status")

    for item in items:
        due = item["next_action_due"] or "N/A"
        company = item["company"] or "Unknown"
        title = (item["title"] or "Untitled")[:25]
        action = (item["next_action"] or "")[:35]
        status = item["tracking_status"] or ""
        table.add_row(due, company, title, action, status)

    console.print(table)
