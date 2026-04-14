"""CLI command: init."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

__all__ = ["init"]


def init(
        resume_json: Optional[Path] = typer.Option(
            None,
            "--resume-json",
            help="Import an existing JSON Resume file during setup.",
        ),
        resume_pdf: Optional[list[Path]] = typer.Option(
            None,
            "--resume-pdf",
            help="Import resume from PDF/TXT files via LLM.",
        ),
) -> None:
    """Run the first-time setup wizard (profile, resume, search config)."""
    from applypilot.bootstrap import get_app

    app = get_app()
    result = app.profile_svc.run_wizard(resume_json=resume_json, resume_pdfs=resume_pdf)
    if not result.success:
        raise typer.Exit(code=1)
