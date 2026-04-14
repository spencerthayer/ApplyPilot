"""Wizard."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from applypilot.config import (
    APP_DIR,
    RESUME_JSON_PATH,
    ensure_dirs,
)
from applypilot.resume_json import (
    normalize_profile_from_resume_json,
)
from applypilot.wizard.resume_setup import _write_resume_json, _decompose_resume, _generate_variants_background

console = Console()


def _discover_and_confirm_tracks(resume_data: dict) -> None:
    """Discover career tracks from profile, confirm with user, persist + generate base resumes."""
    from applypilot.services.track_discovery import discover_tracks_llm, merge_user_tracks
    from applypilot.bootstrap import get_app
    from rich.prompt import Confirm, Prompt
    from rich.table import Table

    console.print("\n[bold cyan]Career Track Discovery[/bold cyan]")
    console.print("[dim]Analyzing your profile for distinct career tracks...[/dim]")

    tracks = discover_tracks_llm(resume_data)

    if tracks:
        # Split into strong/viable vs weak tracks
        viable = [t for t in tracks if t.data_strength in ("strong", "moderate")]
        weak = [t for t in tracks if t.data_strength in ("weak", "unknown")]

        if viable:
            table = Table(title=f"Tracks with Profile Support ({len(viable)})")
            table.add_column("#", style="bold")
            table.add_column("Track")
            table.add_column("Strength")
            table.add_column("Skills")
            for i, t in enumerate(viable, 1):
                strength_color = {"strong": "green", "moderate": "yellow"}.get(t.data_strength, "dim")
                table.add_row(
                    str(i),
                    t.name,
                    f"[{strength_color}]{t.data_strength}[/{strength_color}]",
                    ", ".join(t.skills[:6]) + ("..." if len(t.skills) > 6 else ""),
                )
            console.print(table)

        if weak:
            console.print(f"\n[yellow]⚠ {len(weak)} track(s) detected but your profile has limited data:[/yellow]")
            for t in weak:
                console.print(f"  [yellow]{t.name}[/yellow]")
                if t.gaps:
                    console.print(f"    Missing: {', '.join(t.gaps[:3])}")
                console.print("    [dim]The LLM would drop this — but you can keep it and strengthen later.[/dim]")

            keep_weak = Confirm.ask("Keep these weak tracks anyway?", default=True)
            if not keep_weak:
                for t in weak:
                    t.active = False
                console.print(
                    f"[dim]Deactivated {len(weak)} weak track(s). Re-enable with `applypilot tracks discover` after strengthening.[/dim]"
                )

    # Let user add custom tracks
    user_additions = Prompt.ask(
        "Add more tracks? (comma-separated, or Enter to skip)",
        default="",
    )
    if user_additions.strip():
        tracks = merge_user_tracks(tracks, user_additions.split(","))
        console.print(f"[green]Total tracks: {len(tracks)}[/green]")

    # Let user remove tracks
    if tracks and not Confirm.ask(f"Activate all {len(tracks)} tracks?", default=True):
        for t in tracks:
            t.active = Confirm.ask(f"  Keep '{t.name}'?", default=True)
        dropped = [t for t in tracks if not t.active]
        if dropped:
            console.print(f"\n[yellow]Dropped {len(dropped)} track(s):[/yellow]")
            for t in dropped:
                console.print(f"  [red]✗[/red] {t.name}")
                if t.data_strength == "strong":
                    console.print(
                        f"    [yellow]⚠ This track had strong profile data — "
                        f"jobs matching '{t.name}' will use your generic resume instead.[/yellow]"
                    )
                if t.skills:
                    console.print(f"    Skills no longer prioritized: {', '.join(t.skills[:5])}")
            console.print("[dim]You can re-activate later with `applypilot tracks discover`[/dim]")

    active = [t for t in tracks if t.active]
    if not active:
        console.print("[dim]No tracks activated.[/dim]")
        return

    # Persist
    app = get_app()
    track_repo = app.container.track_repo
    for t in active:
        track_repo.save(t.track_id, t.name, t.skills, t.active)

    console.print(f"[green]Saved {len(active)} tracks.[/green]")

    # Generate per-track base resumes
    _generate_track_base_resumes(resume_data, active)

    # Show weak tracks warning
    weak = [t for t in active if t.data_strength in ("weak", "unknown")]
    if weak:
        console.print(f"\n[yellow]⚠ {len(weak)} track(s) have limited profile data:[/yellow]")
        for t in weak:
            console.print(f"  [yellow]{t.name}[/yellow] — strengthen with `applypilot strengthen --paste`")
            if t.gaps:
                console.print(f"    Gaps: {', '.join(t.gaps[:3])}")


def _generate_track_base_resumes(resume_data: dict, tracks) -> None:
    """Generate a base resume per track — delegates to shared function."""
    from applypilot.services.track_resumes import generate_track_base_resumes

    generate_track_base_resumes(resume_data, tracks)


_PROVIDER_CREDENTIAL_PROMPTS = {
    "gemini": "Gemini API key (from aistudio.google.com)",
    "openrouter": "OpenRouter API key (from openrouter.ai/keys)",
    "openai": "OpenAI API key",
    "anthropic": "Anthropic API key",
    "bedrock": "AWS region",
    "local": "Local LLM endpoint URL",
}

_PROVIDER_MODEL_PROMPTS = {
    "gemini": "Model",
    "openrouter": "Model",
    "openai": "Model",
    "anthropic": "Model",
    "bedrock": "Bedrock model ID",
    "local": "Model name",
}

# ---------------------------------------------------------------------------
# Early LLM bootstrap (needed when PDF import runs before Step 4)
# ---------------------------------------------------------------------------


from applypilot.wizard.resume_setup import _setup_resume, _setup_canonical_resume
from applypilot.wizard.prompts import _prompt_missing_applypilot_fields
from applypilot.wizard.env_setup import (
    _ensure_llm_configured,
    _setup_ai_features,
    _setup_auto_apply,
    _setup_optional_files,
)
from applypilot.wizard.profile_setup import _setup_profile, _setup_searches


def run_wizard(resume_json: Path | None = None, resume_pdfs: list[Path] | None = None) -> None:
    """Run the full interactive setup wizard."""
    console.print()
    console.print(
        Panel.fit(
            "[bold green]ApplyPilot Setup Wizard[/bold green]\n\n"
            "This will create your configuration at:\n"
            f"  [cyan]{APP_DIR}[/cyan]\n\n"
            "You can re-run this anytime with [bold]applypilot init[/bold].",
            border_style="green",
        )
    )

    ensure_dirs()
    console.print(f"[dim]Created {APP_DIR}[/dim]\n")

    # If --resume-pdf was passed, bootstrap LLM and import directly
    if resume_pdfs:
        _ensure_llm_configured()
        from applypilot.resume_ingest import ingest_resumes

        console.print("[dim]Parsing resume(s) via LLM...[/dim]")
        data = ingest_resumes(resume_pdfs)
        _write_resume_json(data)
        console.print(f"[green]Imported resume into {RESUME_JSON_PATH}[/green]")
        from applypilot.resume_json import load_resume_json_from_path

        data = load_resume_json_from_path(RESUME_JSON_PATH)

        # AI enrichment — ask follow-up questions to strengthen resume
        from applypilot.wizard.enrichment import run_enrichment_interview

        data = run_enrichment_interview(data)
        _write_resume_json(data)

        data = _prompt_missing_applypilot_fields(data)
        _write_resume_json(data)
        # Normalize non-English input to English (INIT-06)
        from applypilot.wizard.i18n import normalize_resume_fields, needs_normalization

        if any(needs_normalization(h) for j in data.get("work", []) for h in j.get("highlights", [])):
            console.print("[dim]Normalizing non-English content...[/dim]")
            data = normalize_resume_fields(data)
            _write_resume_json(data)
        # Per-section review (INIT-10)
        from applypilot.wizard.review import review_resume_sections

        data = review_resume_sections(data)
        _write_resume_json(data)

        # Profile completeness score (INIT-05)
        from applypilot.scoring.profile_completeness import compute_completeness

        result = compute_completeness(data)
        color = "green" if result["score"] >= 7 else "yellow" if result["score"] >= 5 else "red"
        console.print(f"\n[bold]Profile Completeness:[/bold] [{color}]{result['score']}/10[/{color}]")
        for tip in result["tips"]:
            console.print(f"  [yellow]→[/yellow] {tip}")

        # Resume quality score (INIT-08)
        from applypilot.scoring.resume_quality import compute_quality

        quality = compute_quality(data)
        qcolor = "green" if quality["score"] >= 7 else "yellow" if quality["score"] >= 5 else "red"
        console.print(f"[bold]Resume Quality:[/bold] [{qcolor}]{quality['score']}/10[/{qcolor}]")
        console.print(
            f"  Verb strength: {quality['verb_strength']:.0%} | Quantified: {quality['quantified_bullets']}/{quality['total_bullets']}"
        )
        for fb in quality["feedback"][:3]:
            console.print(f"  [yellow]→[/yellow] [{fb['company']}] {fb['issue']}")
        canonical_result = (data, normalize_profile_from_resume_json(data))
    else:
        canonical_result = _setup_canonical_resume(resume_json=resume_json)

    # Also offer enrichment for JSON Resume imports
    if canonical_result is not None and not resume_pdfs:
        canonical_resume, _ = canonical_result
        from applypilot.llm_provider import detect_llm_provider
        from applypilot.config import load_env

        load_env()
        if detect_llm_provider() is not None:
            from applypilot.wizard.enrichment import run_enrichment_interview

            enriched = run_enrichment_interview(canonical_resume)
            if enriched is not canonical_resume:
                _write_resume_json(enriched)
                canonical_result = (enriched, normalize_profile_from_resume_json(enriched))

    if canonical_result is None:
        # Step 1: Resume
        _setup_resume()
        console.print()

        # Step 2: Profile
        profile = _setup_profile()
        console.print()
        canonical_resume = None
    else:
        canonical_resume, profile = canonical_result
        console.print(f"[green]Canonical resume ready:[/green] {RESUME_JSON_PATH}")
        _decompose_resume(canonical_resume)
        # Start variant generation in background — non-blocking, init continues
        _variant_thread = _generate_variants_background(canonical_resume)

        # Track discovery (P2 — INIT-11/12)
        _discover_and_confirm_tracks(canonical_resume)
        console.print()

    # Step 3: Search config
    _setup_searches()
    console.print()

    # Step 4: AI features (optional LLM) — skip if already configured during PDF import
    from applypilot.llm_provider import detect_llm_provider
    from applypilot.config import load_env

    load_env()
    if detect_llm_provider() is None:
        _setup_ai_features()
    else:
        console.print("[green]AI provider already configured.[/green]")
    console.print()

    # Step 4b: Generate relevance filter for discovery (needs LLM)
    load_env()
    if detect_llm_provider() is not None and canonical_resume:
        try:
            from applypilot.discovery.relevance_gate import generate_relevance_filter

            console.print("[dim]Generating job relevance filter from your profile...[/dim]")
            rf = generate_relevance_filter(canonical_resume)
            if rf and rf.get("role_keywords"):
                canonical_resume.setdefault("meta", {}).setdefault("applypilot", {})["relevance_filter"] = rf
                _write_resume_json(canonical_resume)
                console.print(
                    f"[green]Relevance filter saved:[/green] {len(rf['role_keywords'])} role keywords, "
                    f"{len(rf.get('anti_keywords', []))} anti-keywords"
                )
        except Exception as e:
            console.print(f"[yellow]Relevance filter generation skipped: {e}[/yellow]")
    console.print()

    # Step 5: Auto-apply agent
    _setup_auto_apply()
    console.print()

    # Step 6: Optional documents (profile photo, ID, certs)
    _setup_optional_files(profile, canonical_resume=canonical_resume)
    console.print()

    # Done — show tier status
    from applypilot.config import get_tier, TIER_LABELS, TIER_COMMANDS

    tier = get_tier()

    tier_lines: list[str] = []
    for t in range(1, 4):
        label = TIER_LABELS[t]
        cmds = ", ".join(f"[bold]{c}[/bold]" for c in TIER_COMMANDS[t])
        if t <= tier:
            tier_lines.append(f"  [green]✓ Tier {t} — {label}[/green]  ({cmds})")
        elif t == tier + 1:
            tier_lines.append(f"  [yellow]→ Tier {t} — {label}[/yellow]  ({cmds})")
        else:
            tier_lines.append(f"  [dim]✗ Tier {t} — {label}  ({cmds})[/dim]")

    unlock_hint = ""
    if tier == 1:
        unlock_hint = "\n[dim]To unlock Tier 2: configure an LLM provider (re-run [bold]applypilot init[/bold]).[/dim]"
    elif tier == 2:
        unlock_hint = (
            "\n[dim]To unlock Tier 3: install Codex CLI and run `codex login`, or install Claude Code CLI, "
            "plus Chrome and Node.js.[/dim]"
        )

    console.print(
        Panel.fit(
            "[bold green]Setup complete![/bold green]\n\n"
            f"[bold]Your tier: Tier {tier} — {TIER_LABELS[tier]}[/bold]\n\n" + "\n".join(tier_lines) + unlock_hint,
            border_style="green",
        )
    )
