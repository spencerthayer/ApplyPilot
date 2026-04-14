"""CLI command: profile."""

from __future__ import annotations

import applypilot.cli as _cli

console = _cli.console

__all__ = ["profile_show"]


def profile_show() -> None:
    """Show current profile summary."""
    _cli._bootstrap()

    from applypilot.bootstrap import get_app

    app = get_app()
    summary = app.profile.summary()

    console.print("\n[bold]ApplyPilot Profile[/bold]\n")
    console.print(f"  Name:        {summary['name']}")
    console.print(f"  Root:        {summary['root']}")
    console.print(f"  Initialized: {summary['initialized']}")
    console.print(f"  Profile:     {summary['has_profile']}")
    console.print(f"  Database:    {summary['db_exists']}")

    # Piece stats
    result = app.resume_svc.get_pieces()
    if result.success and result.data["count"] > 0:
        pieces = result.data["pieces"]
        type_counts = {}
        for p in pieces:
            type_counts[p.piece_type] = type_counts.get(p.piece_type, 0) + 1
        console.print(f"\n  Resume pieces: {result.data['count']}")
        for t, c in sorted(type_counts.items()):
            console.print(f"    {t}: {c}")

    # Pipeline counts
    counts = app.container.job_repo.get_pipeline_counts()
    if counts["total"] > 0:
        console.print(
            f"\n  Jobs: {counts['total']} total, {counts['applied']} applied, {counts['ready_to_apply']} ready"
        )

    console.print()
