"""CLI command: enrich — AI-guided resume strengthening."""

from __future__ import annotations

import typer

import applypilot.cli as _cli

console = _cli.console

__all__ = ["enrich"]


def enrich(
        voice: bool = typer.Option(False, "--voice", "-v", help="Record audio and transcribe to extract achievements."),
        paste: bool = typer.Option(False, "--paste", "-p", help="Paste free-form text about your experience."),
        questions: int = typer.Option(5, "--questions", "-n", help="Number of AI follow-up questions (default mode)."),
        duration: int = typer.Option(60, "--duration", "-d", help="Voice recording duration in seconds."),
) -> None:
    """Strengthen your resume: AI questions, paste text, or voice input."""
    _cli._bootstrap()

    from applypilot.bootstrap import get_app
    from applypilot.config import check_tier, RESUME_JSON_PATH
    import json

    app = get_app()
    resume = app.profile.load_resume_json()
    if not resume:
        console.print("[red]No resume.json found.[/red] Run 'applypilot init' first.")
        raise typer.Exit(code=1)

    check_tier(2, "AI enrichment")

    if voice:
        from applypilot.wizard.voice_input import record_audio, extract_from_text, integrate_achievements
        from rich.prompt import Confirm

        transcript = record_audio(duration=duration)
        if not transcript:
            raise typer.Exit(code=1)
        console.print(f"\n[dim]Transcript ({len(transcript)} chars):[/dim]")
        console.print(f"[italic]{transcript[:500]}{'...' if len(transcript) > 500 else ''}[/italic]\n")

        extracted = extract_from_text(transcript, resume)
        achievements = extracted.get("achievements", [])
        if not achievements:
            console.print("[yellow]No achievements extracted from audio.[/yellow]")
            return

        for ach in achievements:
            conf = ach.get("confidence", "?")
            color = "green" if conf == "high" else "yellow"
            console.print(f"  [{color}]{conf}[/{color}] {ach.get('bullet', '?')}")

        if Confirm.ask("\nIntegrate into resume?", default=True):
            enriched = integrate_achievements(resume, extracted)
        else:
            return

    elif paste:
        from applypilot.wizard.voice_input import run_text_input

        enriched = run_text_input(resume)

    else:
        from applypilot.wizard.enrichment import run_enrichment_interview

        enriched = run_enrichment_interview(resume)

    if enriched is not resume:
        RESUME_JSON_PATH.write_text(
            json.dumps(enriched, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        console.print(f"[green]Updated:[/green] {RESUME_JSON_PATH}")

        # Validate bullet quality (INIT-21)
        try:
            from applypilot.scoring.star_validator import validate_all_bullets

            issues = [b for b in validate_all_bullets(enriched) if not b["valid"]]
            if issues:
                console.print(f"\n[yellow]⚠ {len(issues)} bullet(s) could be stronger:[/yellow]")
                for i in issues[:5]:
                    console.print(f"  [{i['company']}] {i['bullet'][:80]}")
                    for iss in i["issues"]:
                        console.print(f"    [dim]→ {iss}[/dim]")
        except Exception:
            pass

        result = app.resume_svc.decompose(enriched)
        if result.success:
            console.print(f"[green]Re-decomposed:[/green] {result.data['pieces']} pieces")
