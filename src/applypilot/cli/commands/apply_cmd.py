"""CLI command: apply."""

from __future__ import annotations

import logging
from typing import Optional

import typer

import applypilot.cli as _cli

console = _cli.console

__all__ = ["apply"]


def _resolve_auto_apply_settings(agent: Optional[str], agent_model: Optional[str]) -> tuple[str, str | None]:
    """Resolve the selected auto-apply backend and model override."""

    from applypilot.config import AUTO_APPLY_AGENT_CHOICES, resolve_auto_apply_agent

    if agent is not None and agent not in AUTO_APPLY_AGENT_CHOICES:
        console.print(
            f"[red]Invalid --agent value:[/red] '{agent}'. Choose from: {', '.join(AUTO_APPLY_AGENT_CHOICES)}"
        )
        raise typer.Exit(code=1)

    selection = resolve_auto_apply_agent(preferred=agent)
    if selection.resolved is None:
        requested = selection.requested
        console.print(
            f"[red]Selected auto-apply agent unavailable:[/red] {requested}\n"
            "Install Codex CLI and run [bold]codex login[/bold], or install Claude Code CLI."
        )
        raise typer.Exit(code=1)

    effective_model = agent_model.strip() if agent_model and agent_model.strip() else selection.model
    return selection.resolved, effective_model


def _resolve_backend_option(
        agent: Optional[str],
        backend: Optional[str],
        agent_model: Optional[str],
) -> tuple[str, str | None]:
    """Resolve canonical backend selection, allowing --backend as a strict alias."""

    if agent and backend and agent.strip().lower() != backend.strip().lower():
        console.print("[red]--agent and --backend must match when both are provided.[/red]")
        raise typer.Exit(code=1)
    requested_agent = agent if agent is not None else backend
    return _resolve_auto_apply_settings(requested_agent, agent_model)


def apply(
        limit: Optional[int] = typer.Option(
            None,
            "--limit",
            "-l",
            help="Max applications to submit (default: all currently eligible jobs).",
        ),
        workers: int = typer.Option(1, "--workers", "-w", help="Number of parallel browser workers."),
        min_score: int = typer.Option(7, "--min-score", help="Minimum fit score for job selection."),
        agent: Optional[str] = typer.Option(
            None,
            "--agent",
            help="Auto-apply backend: auto, codex, claude, or opencode.",
        ),
        backend: Optional[str] = typer.Option(
            None,
            "--backend",
            help="Compatibility alias for --agent. Must match --agent if both are provided.",
        ),
        agent_model: Optional[str] = typer.Option(
            None,
            "--agent-model",
            "--model",
            "-m",
            help="Browser agent model override. '--model' is kept as a deprecated alias.",
        ),
        opencode_agent: Optional[str] = typer.Option(
            None,
            "--opencode-agent",
            help="OpenCode sub-agent override. Only applies when --agent opencode is selected.",
        ),
        continuous: bool = typer.Option(False, "--continuous", "-c", help="Run forever, polling for new jobs."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Preview actions without submitting."),
        # ADDED: --debug/-d flag for diagnostic output, consistent with `run -d`
        debug: bool = typer.Option(False, "--debug", "-d", help="Show detailed diagnostic output."),
        headless: bool = typer.Option(False, "--headless", help="Run browsers in headless mode."),
        url: Optional[str] = typer.Option(None, "--url", help="Apply to a specific job URL."),
        gen: bool = typer.Option(False, "--gen", help="Generate prompt file for manual debugging instead of running."),
        mark_applied: Optional[str] = typer.Option(None, "--mark-applied", help="Manually mark a job URL as applied."),
        mark_failed: Optional[str] = typer.Option(
            None, "--mark-failed", help="Manually mark a job URL as failed (provide URL)."
        ),
        fail_reason: Optional[str] = typer.Option(None, "--fail-reason", help="Reason for --mark-failed."),
        reset_failed: bool = typer.Option(False, "--reset-failed", help="Reset all failed jobs for retry."),
) -> None:
    """Launch auto-apply to submit job applications."""
    _cli._bootstrap()

    # Headless server guard — apply requires Chrome
    from applypilot.config.execution_mode import is_headless

    if is_headless() and not mark_applied and not mark_failed and not reset_failed and not gen:
        console.print(
            "[red]Apply requires a display (Chrome). Set APPLYPILOT_MODE=headful or run on a machine with a display.[/red]"
        )
        raise typer.Exit(1)

    if debug:
        logging.getLogger("applypilot").setLevel(logging.DEBUG)

    from applypilot.config import check_tier, load_profile
    from applypilot.resume_json import ResumeJsonError

    # --- Utility modes (no Chrome/browser agent needed) ---

    if mark_applied:
        from applypilot.bootstrap import get_app

        get_app().apply_svc.mark_job(mark_applied, "applied")
        console.print(f"[green]Marked as applied:[/green] {mark_applied}")
        return

    if mark_failed:
        from applypilot.bootstrap import get_app

        get_app().apply_svc.mark_job(mark_failed, "failed", reason=fail_reason)
        console.print(f"[yellow]Marked as failed:[/yellow] {mark_failed} ({fail_reason or 'manual'})")
        return

    if reset_failed:
        from applypilot.bootstrap import get_app

        result = get_app().apply_svc.reset_failed()
        count = result.data.get("reset_count", 0) if result.data else 0
        console.print(f"[green]Reset {count} failed job(s) for retry.[/green]")
        return

    # --- Full apply mode ---

    # Check 1: Tier 3 required (browser agent CLI + Chrome + Node.js)
    check_tier(3, "auto-apply")
    resolved_agent, resolved_model = _resolve_backend_option(agent, backend, agent_model)

    # Check 2: Profile exists or can be repaired from canonical resume.json.
    try:
        load_profile()
    except FileNotFoundError:
        console.print("[red]Profile not found.[/red]\nRun [bold]applypilot init[/bold] to create your profile first.")
        raise typer.Exit(code=1)
    except ResumeJsonError as exc:
        console.print(f"[red]Profile load failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        console.print(f"[red]Profile load failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    # Check 3: Tailored resumes exist (skip for --gen with --url)
    if not (gen and url):
        from applypilot.bootstrap import get_app

        pending = get_app().container.job_repo.get_by_stage("pending_apply")
        if not pending:
            console.print(
                "[red]No tailored resumes ready.[/red]\n"
                "Run [bold]applypilot run score tailor[/bold] first to prepare applications."
            )
            raise typer.Exit(code=1)

    if gen:
        from applypilot.bootstrap import get_app
        from applypilot.apply.backends import build_manual_command

        target = url or ""
        if not target:
            console.print("[red]--gen requires --url to specify which job.[/red]")
            raise typer.Exit(code=1)
        result = get_app().apply_svc.gen_prompt(target, min_score=min_score, model=resolved_model)
        if not result.success:
            console.print(f"[red]{result.error}[/red]")
            raise typer.Exit(code=1)
        prompt_file = result.data["prompt_file"]
        console.print(f"[green]Wrote prompt to:[/green] {prompt_file}")
        console.print("\n[bold]Run manually:[/bold]")
        console.print(f"  {build_manual_command(resolved_agent, prompt_file, 0, resolved_model)}")
        return

    from applypilot.apply.launcher import main as apply_main

    if limit is not None and limit < 0:
        console.print("[red]--limit must be 0 or greater.[/red]")
        raise typer.Exit(code=1)

    effective_limit = None if continuous or limit in (None, 0) else limit
    limit_label = "unlimited" if continuous else ("all available" if effective_limit is None else effective_limit)

    console.print(
        "[yellow]Security: Auto-apply runs with --permission-mode bypassPermissions. "
        "Review generated prompts before use.[/yellow]"
    )
    console.print("\n[bold blue]Launching Auto-Apply[/bold blue]")
    console.print(f"  Limit:    {limit_label}")
    console.print(f"  Workers:  {workers}")
    console.print(f"  Agent:    {resolved_agent}")
    console.print(f"  Model:    {resolved_model or '(default)'}")
    if opencode_agent:
        console.print(f"  OpenCode: {opencode_agent}")
    console.print(f"  Headless: {headless}")
    console.print(f"  Dry run:  {dry_run}")
    if url:
        console.print(f"  Target:   {url}")
    console.print()

    apply_main(
        limit=effective_limit,
        target_url=url,
        min_score=min_score,
        headless=headless,
        agent=resolved_agent,
        model=resolved_model,
        opencode_agent=opencode_agent,
        dry_run=dry_run,
        continuous=continuous,
        workers=workers,
    )
