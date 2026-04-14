"""ApplyPilot CLI — the main entry point."""

from __future__ import annotations

import inspect
import logging
from datetime import datetime
from importlib import metadata as importlib_metadata

import typer
from rich.console import Console

from applypilot import __version__
from applypilot.cli_greenhouse import app as greenhouse_app


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

    _file_fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s", datefmt="%H:%M:%S")
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        bootstrap_log.debug("Could not create log directory %s: %s", LOG_DIR, exc)
        return
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    # Global run log — captures all stages (discover, enrich, score, tailor, cover)
    run_log = logging.getLogger("applypilot")
    run_log_file = LOG_DIR / f"{ts}_run.log"
    if not any(
            isinstance(h, logging.FileHandler) and str(run_log_file) in getattr(h, "baseFilename", "")
            for h in run_log.handlers
    ):
        try:
            run_fh = logging.FileHandler(run_log_file, encoding="utf-8", delay=True)
            run_fh.setFormatter(_file_fmt)
            run_log.addHandler(run_fh)
        except OSError as exc:
            bootstrap_log.debug("Could not open run log: %s", exc)

    # Rotate: keep only last 50 log files
    try:
        log_files = sorted(LOG_DIR.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
        for old in log_files[50:]:
            old.unlink(missing_ok=True)
        agent_files = sorted(LOG_DIR.glob("agent_*.txt"), key=lambda f: f.stat().st_mtime, reverse=True)
        for old in agent_files[50:]:
            old.unlink(missing_ok=True)
    except Exception:
        pass

    # Verbose tailor/cover logs — routed to separate files (too noisy for terminal)
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
config_app = typer.Typer(help="View and manage runtime configuration.")
app.add_typer(greenhouse_app, name="greenhouse")
app.add_typer(resume_app, name="resume")
app.add_typer(config_app, name="config")
cv_app = typer.Typer(help="Generate comprehensive CV.")
app.add_typer(cv_app, name="cv")

# Company subcommand
_company_app = typer.Typer(name="company", help="Manage the company registry.")
app.add_typer(_company_app, name="company")

# LLM subcommand — lazy import to avoid circular dep with cli.console
_llm_app = typer.Typer(name="llm", help="LLM usage and cost reporting.")


@_llm_app.command("costs")
def _llm_costs() -> None:
    """Show accumulated LLM costs for this session."""
    from applypilot.cli.commands.llm_cmd import costs

    costs()


app.add_typer(_llm_app, name="llm")

# Analytics subcommand — lazy import
_analytics_app = typer.Typer(name="analytics", help="Pipeline analytics and insights.")


@_analytics_app.command("report")
def _analytics_report() -> None:
    """Show skill gaps, effectiveness, and pool segmentation."""
    _bootstrap()
    from applypilot.cli.commands.analytics_cmd import report

    report()


app.add_typer(_analytics_app, name="analytics")

# Tracks subcommand (P2) — lazy import
_tracks_app = typer.Typer(name="tracks", help="Manage career tracks (P2).")


@_tracks_app.command("list")
def _tracks_list() -> None:
    """List discovered career tracks."""
    _bootstrap()
    from applypilot.cli.commands.tracks_cmd import list_tracks

    list_tracks()


@_tracks_app.command("discover")
def _tracks_discover() -> None:
    """Discover career tracks from master profile."""
    _bootstrap()
    from applypilot.cli.commands.tracks_cmd import discover

    discover()


app.add_typer(_tracks_app, name="tracks")
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
    """Common setup: load env, create dirs, init DB, wire services."""
    from applypilot.bootstrap import get_app

    get_app()  # initializes everything: env, dirs, legacy DB, new DB layer, services


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"[bold]applypilot[/bold] {__version__}")
        raise typer.Exit()


# ---------------------------------------------------------------------------
# App callback
# ---------------------------------------------------------------------------


@app.callback()
def main(
        version: bool = typer.Option(
            False,
            "--version",
            "-V",
            help="Show version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
) -> None:
    """ApplyPilot — AI-powered end-to-end job application pipeline."""


# ---------------------------------------------------------------------------
# Command registration — import functions, attach decorators here
# ---------------------------------------------------------------------------

from applypilot.cli.commands.init_cmd import init as _init
from applypilot.cli.commands.run_cmd import run as _run
from applypilot.cli.commands.apply_cmd import apply as _apply
from applypilot.cli.commands.single_cmd import single as _single
from applypilot.cli.commands.analyze_cmd import analyze as _analyze
from applypilot.cli.commands.status_cmd import status as _status
from applypilot.cli.commands.timeline_cmd import timeline as _timeline
from applypilot.cli.commands.doctor_cmd import doctor as _doctor
from applypilot.cli.commands.resume_cmd import (
    render_resume as _render_resume,
    tailor_cmd as _tailor_cmd,
    pieces_cmd as _pieces_cmd,
    import_resume_cmd as _import_resume_cmd,
)
from applypilot.cli.commands.hitl_cmd import human_review as _human_review, dashboard as _dashboard
from applypilot.cli.commands.reset_cmd import reset as _reset
from applypilot.cli.commands.recover_cmd import recover as _recover
from applypilot.cli.commands.config_cmd import (
    config_show as _config_show,
    config_init as _config_init,
    config_set as _config_set,
)
from applypilot.cli.commands.profile_cmd import profile_show as _profile_show
from applypilot.cli.commands.enrich_cmd import enrich as _strengthen
from applypilot.cli.commands.cv_cmd import cv_render as _cv_render
from applypilot.cli.commands.resume_refresh_cmd import resume_refresh as _resume_refresh
from applypilot.cli.commands.company_cmd import company_add as _company_add, company_list as _company_list

app.command()(_init)
app.command()(_run)
app.command()(_apply)
app.command("tailor")(_tailor_cmd)
app.command("single", hidden=True, deprecated=True)(_single)  # backward-compat alias
app.command()(_analyze)
resume_app.command("render")(_render_resume)
resume_app.command("pieces")(_pieces_cmd)
resume_app.command("import")(_import_resume_cmd)
config_app.command("show")(_config_show)
config_app.command("init")(_config_init)
config_app.command("set")(_config_set)
app.command()(_status)
app.command()(_timeline)
app.command(name="human-review")(_human_review)
app.command()(_dashboard)
app.command()(_doctor)
app.command()(_reset)
app.command()(_recover)
app.command("profile")(_profile_show)
app.command("strengthen")(_strengthen)  # backward compat alias
resume_app.command("strengthen")(_strengthen)  # new canonical location
cv_app.command("render")(_cv_render)
resume_app.command("refresh")(_resume_refresh)
_company_app.command("add")(_company_add)
_company_app.command("list")(_company_list)

if __name__ == "__main__":
    app()

# Backward-compat re-exports for test monkeypatching
from applypilot.cli.commands.apply_cmd import _resolve_backend_option, _resolve_auto_apply_settings  # noqa: F401
from applypilot.cli.commands.analyze_cmd import _load_job_for_analysis  # noqa: F401
from applypilot.config import RESUME_JSON_PATH  # noqa: F401

# Re-export command functions for test access
from applypilot.cli.commands.doctor_cmd import doctor  # noqa: F401
from applypilot.cli.commands.apply_cmd import apply  # noqa: F401
from applypilot.cli.commands.run_cmd import run  # noqa: F401
from applypilot.cli.commands.init_cmd import init  # noqa: F401
from applypilot.cli.commands.single_cmd import single  # noqa: F401
from applypilot.cli.commands.status_cmd import status  # noqa: F401
