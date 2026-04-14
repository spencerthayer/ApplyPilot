"""CLI command: single — backward-compat alias for `run --url`."""

from __future__ import annotations

import logging
from urllib.parse import urlparse, parse_qs

import typer

import applypilot.cli as _cli

console = _cli.console

__all__ = ["single"]


def _normalize_linkedin_url(url: str) -> str:
    """Rewrite LinkedIn session URLs to public /jobs/view/{id} format."""
    parsed = urlparse(url)
    if "linkedin.com" not in (parsed.hostname or ""):
        return url
    params = parse_qs(parsed.query)
    job_id = params.get("currentJobId", [None])[0]
    if job_id:
        return f"https://www.linkedin.com/jobs/view/{job_id}"
    return url


def single(
        url: str = typer.Argument(..., help="Job listing URL (public page with JD + apply button)."),
        skip_apply: bool = typer.Option(False, "--skip-apply", help="Only add and enrich, don't score/tailor."),
        debug: bool = typer.Option(False, "--debug", "-d", help="Show detailed diagnostic output."),
) -> None:
    """[Deprecated] Use 'run --url URL' instead. Kept for backward compatibility."""
    _cli._bootstrap()
    if debug:
        logging.getLogger("applypilot").setLevel(logging.DEBUG)

    url = _normalize_linkedin_url(url)

    from applypilot.pipeline import run_pipeline

    stages = ["enrich", "score", "tailor"] + ([] if skip_apply else ["cover"])
    run_pipeline(urls=[url], stages=stages)

    # Show next-step hint
    from applypilot.bootstrap import get_app

    app = get_app()
    job = app.job_svc.get_by_url(url)
    if job and job.tailored_resume_path:
        console.print(f'\n[bold]Next:[/bold] applypilot apply --url "{url}"')

    # Evaluation report
    if job and job.fit_score:
        try:
            from applypilot.scoring.evaluation_report import generate_evaluation_report
            from applypilot.config import load_resume_json

            profile = load_resume_json()
            score_result = {
                "score": job.fit_score,
                "matched_skills": [],
                "missing_requirements": [],
                "seniority_gap": 0,
                "title": job.title or "",
            }
            if job.score_reasoning:
                import json

                try:
                    reasoning = json.loads(job.score_reasoning)
                    score_result.update(
                        {
                            "matched_skills": reasoning.get("matched_skills", []),
                            "missing_requirements": reasoning.get("missing_requirements", []),
                        }
                    )
                except (json.JSONDecodeError, TypeError):
                    pass

            report = generate_evaluation_report(score_result, profile)
            level = report["level_strategy"]
            console.print(f"\n[bold]Level Strategy:[/bold] {level['strategy']} (gap={level['gap']})")
            for tip in level["sell_plan"][:2]:
                console.print(f"  [green]→[/green] {tip}")
            if level["downlevel_plan"]:
                console.print(f"  [yellow]If downleveled:[/yellow] {level['downlevel_plan'][0]}")

            pers = report["personalization"]
            if pers["cv_changes"]:
                console.print("\n[bold]CV Changes:[/bold]")
                for c in pers["cv_changes"][:3]:
                    console.print(f"  [cyan]→[/cyan] {c}")
        except Exception:
            pass
