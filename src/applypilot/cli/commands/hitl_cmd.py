"""CLI commands: human_review, dashboard."""

from __future__ import annotations

import typer

import applypilot.cli as _cli

console = _cli.console

__all__ = ["human_review", "dashboard"]


# ADDED: Wire human_review.py::serve() to CLI. The module existed but was
# never connected. Manual ATS jobs now park as 'needs_human' (see launcher.py),
# and this command presents them in a web UI for human-in-the-loop completion.
def human_review(
        port: int = typer.Option(7373, "--port", help="TCP port for the review UI."),
        no_browser: bool = typer.Option(False, "--no-browser", help="Don't auto-open the browser."),
) -> None:
    """Launch the human-in-the-loop review UI for manual ATS jobs."""
    _cli._bootstrap()
    from applypilot.apply.human_review import serve

    serve(port=port, open_browser=not no_browser)


def dashboard() -> None:
    """Generate and open the HTML dashboard in your browser."""
    _cli._bootstrap()

    from applypilot.view import open_dashboard

    open_dashboard()
