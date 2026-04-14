"""CLI command: llm costs — LLM cost and usage reporting."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

console = Console()


def costs() -> None:
    """Show accumulated LLM costs for this session."""
    from applypilot.llm import get_cost_summary

    s = get_cost_summary()

    if s["calls"] == 0:
        console.print("[dim]No LLM calls recorded this session.[/dim]")
        return

    console.print("[bold]LLM Cost Summary[/bold]")
    console.print(f"  Calls:       {s['calls']}")
    console.print(f"  Tokens in:   {s['total_tokens_in']:,}")
    console.print(f"  Tokens out:  {s['total_tokens_out']:,}")
    console.print(f"  Est. cost:   ${s['total_cost']:.4f}")

    if by_model := s.get("by_model"):
        table = Table(title="Cost by Model")
        table.add_column("Model", style="bold")
        table.add_column("Calls", justify="right")
        table.add_column("Tokens In", justify="right")
        table.add_column("Tokens Out", justify="right")
        table.add_column("Est. Cost", justify="right")
        for model, info in sorted(by_model.items(), key=lambda x: -x[1]["cost"]):
            if isinstance(info, dict):
                table.add_row(
                    model,
                    str(info["calls"]),
                    f"{info['tokens_in']:,}",
                    f"{info['tokens_out']:,}",
                    f"${info['cost']:.4f}",
                )
            else:
                # Legacy format (just a float)
                table.add_row(model, "", "", "", f"${info:.4f}")
        console.print(table)
