"""CLI command: doctor."""

from __future__ import annotations

import json
import os

import applypilot.cli as _cli
from applypilot.config import get_resume_source

__all__ = ["doctor"]


def doctor() -> None:
    """Check your setup and diagnose missing requirements."""
    import shutil
    from applypilot.config import (
        load_env,
        PROFILE_PATH,
        RESUME_JSON_PATH,
        RESUME_PATH,
        RESUME_PDF_PATH,
        SEARCH_CONFIG_PATH,
        get_auto_apply_agent_setting,
        get_auto_apply_agent_statuses,
        get_chrome_path,
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
    jobspy_version, jobspy_params, jobspy_missing = _cli._jobspy_runtime_capabilities()
    if jobspy_version is None:
        results.append(
            (
                "python-jobspy",
                warn_mark,
                "pip install --no-deps python-jobspy && pip install pydantic tls-client requests markdownify regex",
            )
        )
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
        results.append(
            ("Chrome/Chromium", fail_mark, "Install Chrome or set CHROME_PATH env var (needed for auto-apply)")
        )

    # Node.js / npx (for Playwright MCP)
    npx_bin = shutil.which("npx")
    if npx_bin:
        results.append(("Node.js (npx)", ok_mark, npx_bin))
    else:
        results.append(
            (
                "Node.js (npx)",
                fail_mark,
                "Install Node.js 20+ from nodejs.org (needed for auto-apply and resume render)",
            )
        )

    if LOCAL_RESUMED.exists():
        results.append(("Resume theme renderer", ok_mark, str(LOCAL_RESUMED)))
    else:
        results.append(("Resume theme renderer", warn_mark, "Run `npm install` to enable `applypilot resume render`"))

    # CapSolver (optional)
    capsolver = os.environ.get("CAPSOLVER_API_KEY")
    if capsolver:
        results.append(("CapSolver API key", ok_mark, "CAPTCHA solving enabled"))
    else:
        results.append(
            ("CapSolver API key", "[dim]optional[/dim]", "Set CAPSOLVER_API_KEY in .env for CAPTCHA solving")
        )

    # --- Render results ---
    _cli.console.print()
    _cli.console.print("[bold]ApplyPilot Doctor[/bold]\n")

    col_w = max(len(r[0]) for r in results) + 2
    for check, status, note in results:
        pad = " " * (col_w - len(check))
        _cli.console.print(f"  {check}{pad}{status}  [dim]{note}[/dim]")

    _cli.console.print()

    # Tier summary
    from applypilot.config import get_tier, TIER_LABELS

    tier = get_tier()
    _cli.console.print(f"[bold]Current tier: Tier {tier} - {TIER_LABELS[tier]}[/bold]")

    if tier == 1:
        _cli.console.print("[dim]  -> Tier 2 unlocks: scoring, tailoring, cover letters (needs an LLM provider)[/dim]")
        _cli.console.print(
            "[dim]  -> Tier 3 unlocks: auto-apply "
            "(needs Codex logged in, Claude installed, or OpenCode MCP-ready, plus Chrome + Node.js)[/dim]"
        )
    elif tier == 2:
        _cli.console.print(
            "[dim]  -> Tier 3 unlocks: auto-apply "
            "(needs Codex logged in, Claude installed, or OpenCode MCP-ready, plus Chrome + Node.js)[/dim]"
        )

    _cli.console.print()
