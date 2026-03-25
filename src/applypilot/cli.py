"""ApplyPilot CLI — the main entry point."""

from __future__ import annotations

import json
import inspect
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from importlib import metadata as importlib_metadata

import typer
from rich.console import Console
from rich.table import Table

from applypilot import __version__
from applypilot.cli_greenhouse import app as greenhouse_app
from applypilot.config import RESUME_JSON_PATH, get_resume_source, load_resume_text
from applypilot.resume_json import ResumeJsonError


def _configure_logging() -> None:
    """Set consistent logging output for CLI runs."""
    bootstrap_log = logging.getLogger(__name__)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )

    # Keep LiteLLM internals quiet by default; warnings/errors still surface.
    for name in ("LiteLLM", "litellm"):
        noisy = logging.getLogger(name)
        noisy.handlers.clear()
        noisy.setLevel(logging.WARNING)
        noisy.propagate = True

    # Route verbose tailor/cover loggers to a file instead of the terminal.
    # Per-attempt warnings and validation details are useful for debugging
    # but too noisy for normal CLI output.
    from applypilot.config import LOG_DIR
    _file_fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%H:%M:%S")
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        bootstrap_log.debug("Could not create log directory %s: %s", LOG_DIR, exc)
        return
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    for logger_name in ("applypilot.scoring.tailor", "applypilot.scoring.cover_letter"):
        file_log = logging.getLogger(logger_name)
        file_log.propagate = False  # suppress terminal output
        if any(isinstance(handler, logging.FileHandler) for handler in file_log.handlers):
            continue
        log_filename = f"{ts}_tailor.log" if logger_name == "applypilot.scoring.tailor" else "cover_letter.log"
        delay_open = logger_name == "applypilot.scoring.tailor"
        try:
            fh = logging.FileHandler(LOG_DIR / log_filename, encoding="utf-8", delay=delay_open)
        except OSError as exc:
            bootstrap_log.debug("Could not open log file for %s: %s", logger_name, exc)
            file_log.propagate = True
            continue
        fh.setFormatter(_file_fmt)
        file_log.addHandler(fh)


_configure_logging()

app = typer.Typer(
    name="applypilot",
    help="AI-powered end-to-end job application pipeline.",
    no_args_is_help=True,
)
resume_app = typer.Typer(help="Manage the canonical JSON Resume artifact.")
app.add_typer(greenhouse_app, name="greenhouse")
app.add_typer(resume_app, name="resume")
console = Console()
log = logging.getLogger(__name__)

# Valid pipeline stages (in execution order)
VALID_STAGES = ("discover", "enrich", "score", "tailor", "cover", "pdf")


def _jobspy_runtime_capabilities() -> tuple[str | None, list[str], list[str]]:
    """Return installed python-jobspy version and capability info."""
    try:
        import jobspy
    except ImportError:
        return None, [], []

    try:
        version = importlib_metadata.version("python-jobspy")
    except importlib_metadata.PackageNotFoundError:
        version = "unknown"

    params = list(inspect.signature(jobspy.scrape_jobs).parameters)
    expected = ["hours_old", "description_format", "linkedin_fetch_description", "proxies"]
    missing = [name for name in expected if name not in params]
    return version, params, missing


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bootstrap() -> None:
    """Common setup: load env, create dirs, init DB."""
    from applypilot.config import load_env, ensure_dirs
    from applypilot.database import init_db

    load_env()
    ensure_dirs()
    init_db()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"[bold]applypilot[/bold] {__version__}")
        raise typer.Exit()


def _resolve_auto_apply_settings(agent: Optional[str], agent_model: Optional[str]) -> tuple[str, str | None]:
    """Resolve the selected auto-apply backend and model override."""

    from applypilot.config import AUTO_APPLY_AGENT_CHOICES, resolve_auto_apply_agent

    if agent is not None and agent not in AUTO_APPLY_AGENT_CHOICES:
        console.print(
            f"[red]Invalid --agent value:[/red] '{agent}'. "
            f"Choose from: {', '.join(AUTO_APPLY_AGENT_CHOICES)}"
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


def _load_job_for_analysis(url: Optional[str], job_id: Optional[int]) -> dict:
    """Load a job from the database for the analyze command."""

    from applypilot.database import get_connection

    conn = get_connection()
    if job_id is not None:
        row = conn.execute(
            """
            SELECT url, title, site, application_url, full_description
            FROM jobs
            WHERE id = ?
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
    elif url is not None:
        like = f"%{url.split('?')[0].rstrip('/')}%"
        row = conn.execute(
            """
            SELECT url, title, site, application_url, full_description
            FROM jobs
            WHERE url = ? OR application_url = ? OR url LIKE ? OR application_url LIKE ?
            LIMIT 1
            """,
            (url, url, like, like),
        ).fetchone()
    else:
        raise typer.Exit(code=1)

    if row is None:
        target = f"id={job_id}" if job_id is not None else url
        console.print(f"[red]No matching job found:[/red] {target}")
        raise typer.Exit(code=1)
    return dict(row)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """ApplyPilot — AI-powered end-to-end job application pipeline."""


@app.command()
def init(
    resume_json: Optional[Path] = typer.Option(
        None,
        "--resume-json",
        help="Import an existing JSON Resume file during setup.",
    ),
) -> None:
    """Run the first-time setup wizard (profile, resume, search config)."""
    from applypilot.wizard.init import run_wizard

    run_wizard(resume_json=resume_json)


@app.command()
def run(
    stages: Optional[list[str]] = typer.Argument(
        None,
        help=(
            "Pipeline stages to run. "
            f"Valid: {', '.join(VALID_STAGES)}, all. "
            "Defaults to 'all' if omitted."
        ),
    ),
    min_score: int = typer.Option(7, "--min-score", help="Minimum fit score for tailor/cover stages."),
    limit: int = typer.Option(0, "--limit", "-l", help="Max jobs per tailor/cover batch (0 = all eligible)."),
    workers: int = typer.Option(1, "--workers", "-w", help="Parallel threads for discovery/enrichment stages."),
    stream: bool = typer.Option(False, "--stream", help="Run stages concurrently (streaming mode)."),
    chunked: bool = typer.Option(True, "--chunked/--no-chunk", help="Chunked mode: overlap discover→enrich→score (default: on)."),
    chunk_size: int = typer.Option(1000, "--chunk-size", help="Jobs per chunk in chunked mode."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview stages without executing."),
    # ADDED: Debug flag to show detailed scoring output (keywords, reasoning)
    debug: bool = typer.Option(False, "--debug", "-d", help="Show detailed scoring output (keywords, reasoning)."),
    validation: str = typer.Option(
        "normal",
        "--validation",
        help=(
            "Validation strictness for tailor/cover stages. "
            "strict: banned words = errors, judge must pass. "
            "normal: banned words = warnings only (default, recommended for Gemini free tier). "
            "lenient: banned words ignored, LLM judge skipped (fastest, fewest API calls)."
        ),
    ),
) -> None:
    """Run pipeline stages: discover, enrich, score, tailor, cover, pdf."""
    _bootstrap()

    # CHANGED: -d sets ALL applypilot loggers to DEBUG, not just scoring.
    # This enables diagnostic output across discovery, enrichment, scoring,
    # tailoring, and cover letter stages.
    if debug:
        logging.getLogger("applypilot").setLevel(logging.DEBUG)
        # Enable litellm request/response logging for full LLM diagnostics
        import litellm
        litellm.set_verbose = True

    from applypilot.pipeline import run_pipeline

    stage_list = stages if stages else ["all"]

    # Validate stage names
    for s in stage_list:
        if s != "all" and s not in VALID_STAGES:
            console.print(
                f"[red]Unknown stage:[/red] '{s}'. "
                f"Valid stages: {', '.join(VALID_STAGES)}, all"
            )
            raise typer.Exit(code=1)

    # Gate AI stages behind Tier 2
    llm_stages = {"score", "tailor", "cover"}
    if any(s in stage_list for s in llm_stages) or "all" in stage_list:
        from applypilot.config import check_tier
        check_tier(2, "AI scoring/tailoring")

    # Validate the --validation flag value
    valid_modes = ("strict", "normal", "lenient")
    if validation not in valid_modes:
        console.print(
            f"[red]Invalid --validation value:[/red] '{validation}'. "
            f"Choose from: {', '.join(valid_modes)}"
        )
        raise typer.Exit(code=1)

    result = run_pipeline(
        stages=stage_list,
        min_score=min_score,
        limit=limit,
        dry_run=dry_run,
        stream=stream,
        chunked=chunked,
        chunk_size=chunk_size,
        workers=workers,
        validation_mode=validation,
    )

    if result.get("errors"):
        raise typer.Exit(code=1)


@app.command()
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
    mark_failed: Optional[str] = typer.Option(None, "--mark-failed", help="Manually mark a job URL as failed (provide URL)."),
    fail_reason: Optional[str] = typer.Option(None, "--fail-reason", help="Reason for --mark-failed."),
    reset_failed: bool = typer.Option(False, "--reset-failed", help="Reset all failed jobs for retry."),
) -> None:
    """Launch auto-apply to submit job applications."""
    _bootstrap()

    # ADDED: -d sets all applypilot loggers to DEBUG, consistent with `run -d`
    if debug:
        logging.getLogger("applypilot").setLevel(logging.DEBUG)

    from applypilot.config import check_tier, load_profile
    from applypilot.database import get_connection

    # --- Utility modes (no Chrome/browser agent needed) ---

    if mark_applied:
        from applypilot.apply.launcher import mark_job
        mark_job(mark_applied, "applied")
        console.print(f"[green]Marked as applied:[/green] {mark_applied}")
        return

    if mark_failed:
        from applypilot.apply.launcher import mark_job
        mark_job(mark_failed, "failed", reason=fail_reason)
        console.print(f"[yellow]Marked as failed:[/yellow] {mark_failed} ({fail_reason or 'manual'})")
        return

    if reset_failed:
        from applypilot.apply.launcher import reset_failed as do_reset
        count = do_reset()
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
        console.print(
            "[red]Profile not found.[/red]\n"
            "Run [bold]applypilot init[/bold] to create your profile first."
        )
        raise typer.Exit(code=1)
    except ResumeJsonError as exc:
        console.print(f"[red]Profile load failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        console.print(f"[red]Profile load failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    # Check 3: Tailored resumes exist (skip for --gen with --url)
    if not (gen and url):
        conn = get_connection()
        ready = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE tailored_resume_path IS NOT NULL AND applied_at IS NULL"
        ).fetchone()[0]
        if ready == 0:
            console.print(
                "[red]No tailored resumes ready.[/red]\n"
                "Run [bold]applypilot run score tailor[/bold] first to prepare applications."
            )
            raise typer.Exit(code=1)

    if gen:
        from applypilot.apply.agent_backends import build_manual_command
        from applypilot.apply.launcher import gen_prompt
        target = url or ""
        if not target:
            console.print("[red]--gen requires --url to specify which job.[/red]")
            raise typer.Exit(code=1)
        prompt_file = gen_prompt(target, min_score=min_score, model=resolved_model)
        if not prompt_file:
            console.print("[red]No matching job found for that URL.[/red]")
            raise typer.Exit(code=1)
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


@app.command("tailor")
def tailor_cmd(
    url: Optional[str] = typer.Option(None, "--url", help="Tailor resume for a specific job URL."),
    min_score: int = typer.Option(7, "--min-score", help="Minimum fit score for tailoring."),
    limit: int = typer.Option(0, "--limit", "-l", help="Max jobs to process when --url is not provided."),
    force: bool = typer.Option(False, "--force", help="Regenerate even if a tailored resume already exists."),
    validation: str = typer.Option(
        "normal",
        "--validation",
        help=(
            "Validation strictness: strict, normal, lenient. "
            "Use normal unless you specifically want stricter/faster behavior."
        ),
    ),
) -> None:
    """Generate tailored resume artifacts."""
    _bootstrap()

    from applypilot.config import check_tier
    from applypilot.scoring.tailor import run_tailoring

    valid_modes = ("strict", "normal", "lenient")
    if validation not in valid_modes:
        console.print(
            f"[red]Invalid --validation value:[/red] '{validation}'. "
            f"Choose from: {', '.join(valid_modes)}"
        )
        raise typer.Exit(code=1)

    check_tier(2, "resume tailoring")

    result = run_tailoring(
        min_score=min_score,
        limit=limit,
        validation_mode=validation,
        target_url=url,
        force=force,
    )
    console.print(
        "[green]Tailoring finished:[/green] "
        f"{result.get('approved', 0)} approved, "
        f"{result.get('failed', 0)} failed, "
        f"{result.get('errors', 0)} errors."
    )


# ADDED: Single-job pipeline — scoped enrich→score→tailor→cover for one URL.
# Uses Pipeline.for_job() which sets ctx.job_url so all stages only touch
# the target job. Avoids scoring/tailoring the entire DB.
@app.command()
def single(
    url: str = typer.Argument(..., help="Job listing URL (public page with JD + apply button)."),
    skip_apply: bool = typer.Option(False, "--skip-apply", help="Only add and enrich, don't score/tailor."),
    # ADDED: --debug/-d flag for diagnostic output, consistent with `run -d`
    debug: bool = typer.Option(False, "--debug", "-d", help="Show detailed diagnostic output."),
) -> None:
    """Scoped pipeline for one job: enrich, score, tailor, cover letter."""
    _bootstrap()
    if debug:
        logging.getLogger("applypilot").setLevel(logging.DEBUG)
    from datetime import datetime, timezone
    from applypilot.database import get_connection, commit_with_retry
    from applypilot.pipeline import Pipeline

    conn = get_connection()
    existing = conn.execute("SELECT url, title, fit_score FROM jobs WHERE url = ?", (url,)).fetchone()
    if existing:
        console.print(f"[yellow]Already in DB:[/yellow] {existing[1] or 'untitled'} (score={existing[2]})")
        return

    slug_title = url.rstrip("/").split("/")[-1].replace("-", " ").replace("_", " ")
    conn.execute(
        "INSERT INTO jobs (url, title, site, strategy, discovered_at) VALUES (?, ?, 'manual', 'manual', ?)",
        (url, slug_title, datetime.now(timezone.utc).isoformat()),
    )
    commit_with_retry(conn)
    console.print(f"[green]Added to DB:[/green] {slug_title}")

    p = Pipeline.for_job(url).enrich().score().tailor()
    if not skip_apply:
        p.cover()
    p.execute()

    row = conn.execute("SELECT tailored_resume_path, cover_letter_path FROM jobs WHERE url = ?", (url,)).fetchone()
    if row and row[0]:
        console.print(f"\n[bold]Next:[/bold] applypilot apply --url \"{url}\"")


@app.command()
def analyze(
    url: Optional[str] = typer.Option(None, "--url", help="Analyze a job already stored in the database by URL."),
    job_id: Optional[int] = typer.Option(None, "--job-id", help="Analyze a job already stored in the database by row id."),
    text_file: Optional[Path] = typer.Option(None, "--text-file", help="Analyze a job description from a local text file."),
    resume_file: Optional[Path] = typer.Option(None, "--resume-file", help="Override the resume text used for match analysis."),
) -> None:
    """Analyze a job description and optional resume match."""
    _bootstrap()

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
        description = row.get("full_description") or ""
        if not description.strip():
            console.print("[red]Job has no full_description yet. Run `applypilot run enrich` first.[/red]")
            raise typer.Exit(code=1)
        job = {
            "title": row.get("title") or "Unknown",
            "company": row.get("site") or "Unknown",
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


@resume_app.command("render")
def render_resume(
    theme: Optional[str] = typer.Option(None, "--theme", help="Theme package name, e.g. jsonresume-theme-even."),
    format: str = typer.Option("html", "--format", help="Output format: html or pdf."),
    output: Optional[Path] = typer.Option(None, "--output", help="Optional output path."),
) -> None:
    """Render the canonical resume.json with a JSON Resume theme."""
    _bootstrap()

    if format not in {"html", "pdf"}:
        console.print("[red]Invalid --format value.[/red] Choose 'html' or 'pdf'.")
        raise typer.Exit(code=1)

    if not RESUME_JSON_PATH.exists():
        console.print(f"[red]Canonical resume not found:[/red] {RESUME_JSON_PATH}")
        raise typer.Exit(code=1)

    from applypilot.resume_render import render_resume_html, render_resume_pdf

    try:
        if format == "html":
            rendered_path, resolved_theme = render_resume_html(theme=theme, output_path=output)
        else:
            rendered_path, resolved_theme = render_resume_pdf(theme=theme, output_path=output)
    except Exception as exc:
        console.print(f"[red]Resume render failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"[green]Rendered {format.upper()}[/green] using [bold]{resolved_theme}[/bold] -> {rendered_path}"
    )


@app.command()
def status() -> None:
    """Show pipeline statistics from the database."""
    _bootstrap()

    from applypilot.database import get_stats

    stats = get_stats()

    console.print("\n[bold]ApplyPilot Pipeline Status[/bold]\n")

    # Summary table
    summary = Table(title="Pipeline Overview", show_header=True, header_style="bold cyan")
    summary.add_column("Metric", style="bold")
    summary.add_column("Count", justify="right")

    summary.add_row("Total jobs discovered", str(stats["total"]))
    summary.add_row("With full description", str(stats["with_description"]))
    summary.add_row("Pending enrichment", str(stats["pending_detail"]))
    summary.add_row("Enrichment errors", str(stats["detail_errors"]))
    summary.add_row("Scored by LLM", str(stats["scored"]))
    summary.add_row("Excluded (pre-filter)", str(stats["excluded"]))
    summary.add_row("Pending scoring", str(stats["unscored"]))
    summary.add_row("Tailored resumes", str(stats["tailored"]))
    summary.add_row("Pending tailoring (7+)", str(stats["untailored_eligible"]))
    summary.add_row("Cover letters", str(stats["with_cover_letter"]))
    summary.add_row("Ready to apply", str(stats["ready_to_apply"]))
    summary.add_row("Applied", str(stats["applied"]))
    summary.add_row("Apply errors", str(stats["apply_errors"]))

    console.print(summary)

    # Score distribution
    if stats["score_distribution"]:
        dist_table = Table(title="\nScore Distribution", show_header=True, header_style="bold yellow")
        dist_table.add_column("Score", justify="center")
        dist_table.add_column("Count", justify="right")
        dist_table.add_column("Bar")

        max_count = max(count for _, count in stats["score_distribution"]) or 1
        for score, count in stats["score_distribution"]:
            bar_len = int(count / max_count * 30)
            if score >= 7:
                color = "green"
            elif score >= 5:
                color = "yellow"
            else:
                color = "red"
            bar = f"[{color}]{'=' * bar_len}[/{color}]"
            dist_table.add_row(str(score), str(count), bar)

        console.print(dist_table)

    # By site
    if stats["by_site"]:
        site_table = Table(title="\nJobs by Source", show_header=True, header_style="bold magenta")
        site_table.add_column("Site")
        site_table.add_column("Count", justify="right")

        for site, count in stats["by_site"]:
            site_table.add_row(site or "Unknown", str(count))

        console.print(site_table)

    console.print()


# ADDED: Wire human_review.py::serve() to CLI. The module existed but was
# never connected. Manual ATS jobs now park as 'needs_human' (see launcher.py),
# and this command presents them in a web UI for human-in-the-loop completion.
@app.command(name="human-review")
def human_review(
    port: int = typer.Option(7373, "--port", help="TCP port for the review UI."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't auto-open the browser."),
) -> None:
    """Launch the human-in-the-loop review UI for manual ATS jobs."""
    _bootstrap()
    from applypilot.apply.human_review import serve
    serve(port=port, open_browser=not no_browser)


@app.command()
def dashboard() -> None:
    """Generate and open the HTML dashboard in your browser."""
    _bootstrap()

    from applypilot.view import open_dashboard

    open_dashboard()


@app.command()
def doctor() -> None:
    """Check your setup and diagnose missing requirements."""
    import shutil
    from applypilot.config import (
        load_env, PROFILE_PATH, RESUME_JSON_PATH, RESUME_PATH, RESUME_PDF_PATH,
        SEARCH_CONFIG_PATH, get_auto_apply_agent_setting,
        get_auto_apply_agent_statuses, get_chrome_path,
        resolve_auto_apply_agent,
    )
    from applypilot.llm_provider import format_llm_provider_status, llm_config_hint
    from applypilot.resume_render import LOCAL_RESUMED

    load_env()

    ok_mark = "[green]OK[/green]"
    fail_mark = "[red]MISSING[/red]"
    warn_mark = "[yellow]WARN[/yellow]"

    results: list[tuple[str, str, str]] = []  # (check, status, note)

    # --- Tier 1 checks ---
    resume_source = get_resume_source()
    if PROFILE_PATH.exists():
        try:
            json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            results.append(("profile.json", ok_mark, str(PROFILE_PATH)))
        except json.JSONDecodeError as exc:
            results.append(
                (
                    "profile.json",
                    fail_mark,
                    f"Malformed JSON: line {exc.lineno}, column {exc.colno}: {exc.msg}",
                )
            )
    elif resume_source.mode == "canonical":
        results.append(("profile.json", warn_mark, "Missing, but will be auto-generated from resume.json on next run"))
    elif resume_source.mode == "canonical_invalid":
        results.append(("profile.json", fail_mark, "Missing, and resume.json must be fixed before it can backfill"))
    else:
        results.append(("profile.json", fail_mark, "Run 'applypilot init' to create"))

    if resume_source.mode == "canonical":
        results.append(("resume.json", ok_mark, str(RESUME_JSON_PATH)))
    elif resume_source.mode == "canonical_invalid":
        results.append(("resume.json", fail_mark, resume_source.detail))
    else:
        results.append(("resume.json", warn_mark, "Optional, but required for JSON Resume import/render flows"))

    if resume_source.mode == "canonical":
        if RESUME_PATH.exists():
            results.append(("resume.txt", warn_mark, f"{RESUME_PATH} (legacy fallback still present)"))
        else:
            results.append(("resume.txt", ok_mark, "Not required when resume.json is present"))
    elif resume_source.mode == "canonical_invalid":
        if RESUME_PATH.exists():
            results.append(("resume.txt", warn_mark, f"{RESUME_PATH} (present, but resume.json must be fixed first)"))
        elif RESUME_PDF_PATH.exists():
            results.append(("resume.txt", warn_mark, "Only PDF found - plain-text needed for AI stages"))
        else:
            results.append(("resume.txt", fail_mark, "Run 'applypilot init' to add your resume"))
    elif RESUME_PATH.exists():
        results.append(("resume.txt", ok_mark, str(RESUME_PATH)))
    elif RESUME_PDF_PATH.exists():
        results.append(("resume.txt", warn_mark, "Only PDF found - plain-text needed for AI stages"))
    else:
        results.append(("resume.txt", fail_mark, "Run 'applypilot init' to add your resume"))

    # Search config
    if SEARCH_CONFIG_PATH.exists():
        results.append(("searches.yaml", ok_mark, str(SEARCH_CONFIG_PATH)))
    else:
        results.append(("searches.yaml", warn_mark, "Will use example config - run 'applypilot init'"))

    # jobspy (discovery dep installed separately)
    jobspy_version, jobspy_params, jobspy_missing = _jobspy_runtime_capabilities()
    if jobspy_version is None:
        results.append(("python-jobspy", warn_mark,
                        "pip install --no-deps python-jobspy && pip install pydantic tls-client requests markdownify regex"))
    else:
        results.append(("python-jobspy", ok_mark, f"version {jobspy_version}"))
        if jobspy_missing:
            results.append(
                (
                    "JobSpy capability mode",
                    warn_mark,
                    f"compatibility mode active (missing args: {', '.join(jobspy_missing)})",
                )
            )
        else:
            results.append(("JobSpy capability mode", ok_mark, "full feature signature detected"))
        if jobspy_params:
            results.append(("JobSpy scrape_jobs args", "[dim]info[/dim]", ", ".join(jobspy_params)))

    # --- Tier 2 checks (built-in LLM layer) ---
    llm_status = format_llm_provider_status()
    if llm_status:
        results.append(("Built-in LLM", ok_mark, llm_status))
    else:
        results.append(("Built-in LLM", fail_mark, llm_config_hint()))

    # --- Tier 3 checks (auto-apply agent layer) ---
    requested_agent = get_auto_apply_agent_setting()
    resolved_agent = resolve_auto_apply_agent()
    if resolved_agent.resolved:
        note = f"{requested_agent} -> {resolved_agent.resolved}"
    else:
        note = requested_agent
    if resolved_agent.model:
        note += f" ({resolved_agent.model})"
    results.append(("Auto-apply agent", ok_mark if resolved_agent.resolved else warn_mark, note))

    agent_statuses = get_auto_apply_agent_statuses()

    codex_status = agent_statuses["codex"]
    if codex_status.binary_path:
        results.append(("Codex CLI", ok_mark, codex_status.binary_path))
        results.append(("Codex login", ok_mark if codex_status.available else fail_mark, codex_status.note))
    else:
        results.append(("Codex CLI", fail_mark, codex_status.note))

    claude_status = agent_statuses["claude"]
    if claude_status.binary_path:
        results.append(("Claude Code CLI", ok_mark, claude_status.binary_path))
    else:
        results.append(("Claude Code CLI", "[dim]optional[/dim]", claude_status.note))

    opencode_status = agent_statuses.get("opencode")
    if opencode_status:
        if opencode_status.binary_path:
            results.append(("OpenCode CLI", ok_mark if opencode_status.available else warn_mark, opencode_status.note))
        else:
            results.append(("OpenCode CLI", "[dim]optional[/dim]", opencode_status.note))

    # Chrome
    try:
        chrome_path = get_chrome_path()
        results.append(("Chrome/Chromium", ok_mark, chrome_path))
    except FileNotFoundError:
        results.append(("Chrome/Chromium", fail_mark,
                        "Install Chrome or set CHROME_PATH env var (needed for auto-apply)"))

    # Node.js / npx (for Playwright MCP)
    npx_bin = shutil.which("npx")
    if npx_bin:
        results.append(("Node.js (npx)", ok_mark, npx_bin))
    else:
        results.append(("Node.js (npx)", fail_mark,
                        "Install Node.js 20+ from nodejs.org (needed for auto-apply and resume render)"))

    if LOCAL_RESUMED.exists():
        results.append(("Resume theme renderer", ok_mark, str(LOCAL_RESUMED)))
    else:
        results.append(("Resume theme renderer", warn_mark, "Run `npm install` to enable `applypilot resume render`"))

    # CapSolver (optional)
    capsolver = os.environ.get("CAPSOLVER_API_KEY")
    if capsolver:
        results.append(("CapSolver API key", ok_mark, "CAPTCHA solving enabled"))
    else:
        results.append(("CapSolver API key", "[dim]optional[/dim]",
                        "Set CAPSOLVER_API_KEY in .env for CAPTCHA solving"))

    # --- Render results ---
    console.print()
    console.print("[bold]ApplyPilot Doctor[/bold]\n")

    col_w = max(len(r[0]) for r in results) + 2
    for check, status, note in results:
        pad = " " * (col_w - len(check))
        console.print(f"  {check}{pad}{status}  [dim]{note}[/dim]")

    console.print()

    # Tier summary
    from applypilot.config import get_tier, TIER_LABELS
    tier = get_tier()
    console.print(f"[bold]Current tier: Tier {tier} - {TIER_LABELS[tier]}[/bold]")

    if tier == 1:
        console.print("[dim]  -> Tier 2 unlocks: scoring, tailoring, cover letters (needs an LLM provider)[/dim]")
        console.print(
            "[dim]  -> Tier 3 unlocks: auto-apply "
            "(needs Codex logged in, Claude installed, or OpenCode MCP-ready, plus Chrome + Node.js)[/dim]"
        )
    elif tier == 2:
        console.print(
            "[dim]  -> Tier 3 unlocks: auto-apply "
            "(needs Codex logged in, Claude installed, or OpenCode MCP-ready, plus Chrome + Node.js)[/dim]"
        )

    console.print()


if __name__ == "__main__":
    app()
