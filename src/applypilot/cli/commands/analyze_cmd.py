"""CLI command: analyze."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

import applypilot.cli as _cli

console = _cli.console

__all__ = ["analyze"]


def _load_job_for_analysis(url: Optional[str], job_id: Optional[int]):
    """Load a job from the database for the analyze command."""
    from applypilot.bootstrap import get_app

    repo = get_app().container.job_repo

    if job_id is not None:
        row = repo.get_by_rowid(job_id)
    elif url is not None:
        row = repo.get_by_url(url) or repo.find_by_url_fuzzy(url)
    else:
        raise typer.Exit(code=1)

    if row is None:
        target = f"id={job_id}" if job_id is not None else url
        console.print(f"[red]No matching job found:[/red] {target}")
        raise typer.Exit(code=1)
    return row


def analyze(
        url: Optional[str] = typer.Option(None, "--url", help="Analyze a job already stored in the database by URL."),
        job_id: Optional[int] = typer.Option(
            None, "--job-id", help="Analyze a job already stored in the database by row id."
        ),
        text_file: Optional[Path] = typer.Option(
            None, "--text-file", help="Analyze a job description from a local text file."
        ),
        resume_file: Optional[Path] = typer.Option(
            None, "--resume-file", help="Override the resume text used for match analysis."
        ),
) -> None:
    """Analyze a job description and optional resume match."""
    _cli._bootstrap()

    from applypilot.config import load_resume_text
    from applypilot.resume_json import ResumeJsonError

    provided_sources = sum(1 for value in (url, job_id, text_file) if value is not None)
    if provided_sources != 1:
        console.print("[red]Provide exactly one of --url, --job-id, or --text-file.[/red]")
        raise typer.Exit(code=1)

    if text_file is not None:
        if not text_file.exists():
            console.print(f"[red]File not found:[/red] {text_file}")
            raise typer.Exit(code=1)
        job = {
            "title": text_file.stem.replace("_", " "),
            "company": "Unknown",
            "description": text_file.read_text(encoding="utf-8"),
        }
    else:
        row = _load_job_for_analysis(url, job_id)
        description = row.full_description or ""
        if not description.strip():
            console.print("[red]Job has no full_description yet. Run `applypilot run enrich` first.[/red]")
            raise typer.Exit(code=1)
        job = {
            "title": row.title or "Unknown",
            "company": row.site or "Unknown",
            "description": description,
        }

    from applypilot.intelligence.jd_parser import JobDescriptionParser
    from applypilot.intelligence.resume_matcher import ResumeMatcher

    parser = JobDescriptionParser()
    job_intel = parser.parse(job)
    analysis_output = {
        "title": job_intel.title,
        "company": job_intel.company,
        "seniority": job_intel.seniority.value,
        "requirements": [req.__dict__ for req in job_intel.requirements],
        "skills": [skill.__dict__ for skill in job_intel.skills],
        "key_responsibilities": job_intel.key_responsibilities,
        "red_flags": job_intel.red_flags,
        "company_context": job_intel.company_context,
    }

    try:
        resume_text = load_resume_text(resume_file)
    except FileNotFoundError:
        resume_text = None
    except ResumeJsonError as exc:
        console.print(f"[red]Resume load failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if resume_text is not None:
        matcher = ResumeMatcher()
        match = matcher.analyze(resume_text, job_intel)
        analysis_output["match"] = {
            "overall_score": match.overall_score,
            "strengths": match.strengths,
            "gaps": [gap.__dict__ for gap in match.gaps],
            "recommendations": match.recommendations,
            "bullet_priorities": match.bullet_priorities,
        }

    console.print_json(json.dumps(analysis_output))
