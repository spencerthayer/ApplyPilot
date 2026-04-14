"""Env Setup."""

from __future__ import annotations

import shutil
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from applypilot.config import (
    AUTO_APPLY_AGENT_CHOICES,
    DEFAULT_AUTO_APPLY_AGENT,
    ENV_PATH,
    FILES_DIR,
)
from applypilot.llm_provider import LLM_PROVIDER_SPECS, WIZARD_PROVIDER_ORDER
from applypilot.wizard.profile_setup import _write_profile_json
from applypilot.wizard.resume_setup import _write_resume_json

console = Console()

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


def _ensure_llm_configured() -> None:
    """If no LLM provider is configured yet, prompt for one now and persist."""
    from applypilot.llm_provider import detect_llm_provider
    from applypilot.config import load_env

    load_env()
    provider = detect_llm_provider()
    if provider is not None:
        return

    console.print("\n[yellow]PDF import needs an LLM. Let's configure one first.[/yellow]")
    _setup_ai_features()
    load_env()  # reload so the rest of the process picks up the new keys


def _build_ai_env_lines(provider: str, credential: str, model: str) -> list[str]:
    """Build the .env lines for the selected AI provider."""

    spec = LLM_PROVIDER_SPECS[provider]
    return [
        "# ApplyPilot configuration",
        "",
        f"{spec.env_key}={credential}",
        f"LLM_MODEL={model}",
        "",
    ]


def _parse_env_lines(text: str) -> tuple[list[str], dict[str, int]]:
    lines = text.splitlines()
    positions: dict[str, int] = {}
    for idx, line in enumerate(lines):
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key = line.split("=", 1)[0].strip()
        if key:
            positions[key] = idx
    return lines, positions


def _upsert_env_vars(updates: dict[str, str]) -> None:
    """Merge selected env vars into ~/.applypilot/.env without dropping others."""

    if ENV_PATH.exists():
        lines, positions = _parse_env_lines(ENV_PATH.read_text(encoding="utf-8"))
    else:
        lines = ["# ApplyPilot configuration", ""]
        positions = {}

    for key, value in updates.items():
        rendered = f"{key}={value}"
        if key in positions:
            lines[positions[key]] = rendered
        else:
            if lines and lines[-1] != "":
                lines.append("")
            positions[key] = len(lines)
            lines.append(rendered)

    content = "\n".join(lines).rstrip() + "\n"
    ENV_PATH.write_text(content, encoding="utf-8")


def _delete_env_vars(keys: list[str]) -> None:
    """Remove env vars from ~/.applypilot/.env if they exist."""

    if not ENV_PATH.exists():
        return

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    filtered = [
        line
        for line in lines
        if "=" not in line or line.lstrip().startswith("#") or line.split("=", 1)[0].strip() not in keys
    ]
    ENV_PATH.write_text("\n".join(filtered).rstrip() + "\n", encoding="utf-8")


def _setup_ai_features() -> None:
    """Ask about AI scoring/tailoring — optional LLM configuration."""
    console.print(
        Panel(
            "[bold]Step 4: AI Features (optional)[/bold]\n"
            "An LLM powers job scoring, resume tailoring, and cover letters.\n"
            "Without this, you can still discover and enrich jobs."
        )
    )

    if not Confirm.ask("Enable AI scoring and resume tailoring?", default=True):
        console.print("[dim]Discovery-only mode. You can configure AI later with [bold]applypilot init[/bold].[/dim]")
        return

    console.print(
        "Supported providers: [bold]Gemini[/bold] (recommended, free tier), "
        "OpenRouter (flexible multi-model), OpenAI, Anthropic, "
        "AWS Bedrock (uses ada credentials), local (Ollama/llama.cpp)"
    )
    provider = Prompt.ask(
        "Provider",
        choices=list(WIZARD_PROVIDER_ORDER),
        default="gemini",
    )

    if provider == "bedrock":
        region = Prompt.ask(_PROVIDER_CREDENTIAL_PROMPTS[provider], default="us-east-1")
        model = Prompt.ask(_PROVIDER_MODEL_PROMPTS[provider], default=LLM_PROVIDER_SPECS[provider].default_model)
        other_provider_keys = [entry.env_key for entry in LLM_PROVIDER_SPECS.values() if entry.key != provider]
        _delete_env_vars(other_provider_keys + ["LLM_MODEL"])
        _upsert_env_vars(
            {
                "BEDROCK_MODEL_ID": model,
                "BEDROCK_REGION": region,
            }
        )
    elif provider == "local":
        credential = Prompt.ask(_PROVIDER_CREDENTIAL_PROMPTS[provider], default="http://localhost:8080/v1")
        model = Prompt.ask(_PROVIDER_MODEL_PROMPTS[provider], default=LLM_PROVIDER_SPECS[provider].default_model)
        spec = LLM_PROVIDER_SPECS[provider]
        other_provider_keys = [entry.env_key for entry in LLM_PROVIDER_SPECS.values() if entry.key != provider]
        _delete_env_vars(other_provider_keys)
        _upsert_env_vars({spec.env_key: credential, "LLM_MODEL": model})
    else:
        credential = Prompt.ask(_PROVIDER_CREDENTIAL_PROMPTS[provider])
        model = Prompt.ask(_PROVIDER_MODEL_PROMPTS[provider], default=LLM_PROVIDER_SPECS[provider].default_model)
        spec = LLM_PROVIDER_SPECS[provider]
        other_provider_keys = [entry.env_key for entry in LLM_PROVIDER_SPECS.values() if entry.key != provider]
        _delete_env_vars(other_provider_keys)
        _upsert_env_vars({spec.env_key: credential, "LLM_MODEL": model})
    console.print(f"[green]AI configuration saved to {ENV_PATH}[/green]")

    # Multi-model routing (optional)
    console.print(
        "\n[dim]You can use different models for different tasks:[/dim]\n"
        "  [bold]cheap[/bold]   — scoring, enrichment (high volume, default model above)\n"
        "  [bold]mid[/bold]     — resume tailoring, cover letters (quality matters)\n"
        "  [bold]premium[/bold] — complex agentic decisions (smartest model)\n"
        "[dim]Leave blank to use the same model for everything.[/dim]"
    )
    if Confirm.ask("Configure separate models per tier?", default=False):
        mid_model = Prompt.ask("Mid-tier model (tailoring/cover letters)", default="").strip()
        premium_model = Prompt.ask("Premium model (agentic/complex)", default="").strip()
        tier_updates: dict[str, str] = {}
        tier_deletes: list[str] = []
        if mid_model:
            tier_updates["LLM_MODEL_MID"] = mid_model
        else:
            tier_deletes.append("LLM_MODEL_MID")
        if premium_model:
            tier_updates["LLM_MODEL_PREMIUM"] = premium_model
        else:
            tier_deletes.append("LLM_MODEL_PREMIUM")
        if tier_updates:
            _upsert_env_vars(tier_updates)
        if tier_deletes:
            _delete_env_vars(tier_deletes)
        console.print(f"[green]Multi-model routing saved to {ENV_PATH}[/green]")
    else:
        _delete_env_vars(["LLM_MODEL_MID", "LLM_MODEL_PREMIUM", "LLM_MODEL_QUALITY"])


def _setup_auto_apply() -> None:
    """Configure autonomous job application (separate from the built-in LLM)."""
    from applypilot.config import get_auto_apply_agent_statuses

    console.print(
        Panel(
            "[bold]Step 5: Auto-Apply Agent (optional)[/bold]\n"
            "ApplyPilot can autonomously fill and submit job applications\n"
            "using a browser agent. This is separate from the Gemini/OpenRouter/OpenAI/local\n"
            "LLM you configure for scoring, tailoring, and cover letters."
        )
    )

    if not Confirm.ask("Enable autonomous job applications?", default=True):
        console.print("[dim]You can apply manually using the tailored resumes ApplyPilot generates.[/dim]")
        return

    statuses = get_auto_apply_agent_statuses()
    if statuses["codex"].available:
        console.print(f"[green]Codex CLI ready.[/green] {statuses['codex'].note}")
    elif statuses["codex"].binary_path:
        console.print(f"[yellow]Codex CLI found but not ready.[/yellow] {statuses['codex'].note}")
    else:
        console.print(f"[yellow]Codex CLI not found.[/yellow] {statuses['codex'].note}")

    if statuses["claude"].available:
        console.print("[green]Claude Code CLI detected.[/green]")
    else:
        console.print(
            "[dim]Claude Code CLI optional fallback.[/dim]\n"
            "Install it from: [bold]https://claude.ai/code[/bold] if you want Claude compatibility."
        )

    opencode_status = statuses.get("opencode")
    if opencode_status:
        if opencode_status.available:
            console.print(f"[green]OpenCode CLI ready.[/green] {opencode_status.note}")
        elif opencode_status.binary_path:
            console.print(f"[yellow]OpenCode CLI found but not ready.[/yellow] {opencode_status.note}")
        else:
            console.print(f"[dim]OpenCode CLI optional.[/dim] {opencode_status.note}")

    default_agent = DEFAULT_AUTO_APPLY_AGENT
    if statuses["codex"].available and not statuses["claude"].available:
        default_agent = "codex"
    elif statuses["claude"].available and not statuses["codex"].available:
        default_agent = "claude"
    elif (
            opencode_status
            and opencode_status.available
            and not statuses["codex"].available
            and not statuses["claude"].available
    ):
        default_agent = "opencode"

    selected_agent = Prompt.ask(
        "Browser agent",
        choices=list(AUTO_APPLY_AGENT_CHOICES),
        default=default_agent,
    )
    model_override = Prompt.ask("Browser agent model override (optional)", default="")
    updates = {"AUTO_APPLY_AGENT": selected_agent}
    if selected_agent == "opencode":
        opencode_agent = Prompt.ask("OpenCode sub-agent (optional)", default="").strip()
        if opencode_agent:
            updates["APPLY_OPENCODE_AGENT"] = opencode_agent
        else:
            _delete_env_vars(["APPLY_OPENCODE_AGENT"])
    else:
        _delete_env_vars(["APPLY_OPENCODE_AGENT"])
    if model_override.strip():
        updates["AUTO_APPLY_MODEL"] = model_override.strip()
    else:
        _delete_env_vars(["AUTO_APPLY_MODEL"])
    _upsert_env_vars(updates)
    console.print(f"[green]Auto-apply agent saved to {ENV_PATH}[/green]")

    # Optional: CapSolver for CAPTCHAs
    console.print("\n[dim]Some job sites use CAPTCHAs. CapSolver can handle them automatically.[/dim]")
    if Confirm.ask("Configure CapSolver API key? (optional)", default=False):
        capsolver_key = Prompt.ask("CapSolver API key")
        _upsert_env_vars({"CAPSOLVER_API_KEY": capsolver_key})
        console.print("[green]CapSolver key saved.[/green]")
    else:
        console.print("[dim]Skipped. Add CAPSOLVER_API_KEY to .env later if needed.[/dim]")


def _setup_optional_files(profile: dict, canonical_resume: dict | None = None) -> None:
    """Optionally copy documents into ~/.applypilot/files/ and record paths in profile or resume.json."""
    _OPTIONAL_FILE_KEYS = [
        ("photo", "Profile photo", [".jpg", ".jpeg", ".png"]),
        ("id_document", "ID / Passport", [".jpg", ".jpeg", ".png", ".pdf"]),
        ("cover_letter_template", "Cover letter template", [".docx", ".pdf", ".txt"]),
        ("transcript", "Academic transcript", [".pdf"]),
    ]

    console.print(
        Panel(
            "[bold]Step 6: Optional Documents (skip if not needed)[/bold]\n"
            "Profile photo, ID, passport, certificates — some applications ask for these.\n"
            "Files are copied to [cyan]~/.applypilot/files/[/cyan] for use by the apply agent."
        )
    )

    if not Confirm.ask("Do you have any optional documents to add?", default=False):
        console.print(
            "[dim]Skipped. Add files to ~/.applypilot/files/ and update ~/.applypilot/profile.json later.[/dim]"
        )
        return

    files: dict[str, str] = profile.get("files", {})
    FILES_DIR.mkdir(parents=True, exist_ok=True)

    # Known file types
    for key, label, allowed_exts in _OPTIONAL_FILE_KEYS:
        if not Confirm.ask(f"Add {label}?", default=False):
            continue
        while True:
            path_str = Prompt.ask(f"{label} file path")
            src = Path(path_str.strip().strip('"').strip("'")).expanduser().resolve()
            if not src.exists():
                console.print(f"[red]File not found:[/red] {src}")
                continue
            if src.suffix.lower() not in allowed_exts:
                console.print(f"[red]Unsupported format.[/red] Use one of: {', '.join(allowed_exts)}")
                continue
            dest = FILES_DIR / f"{key}{src.suffix.lower()}"
            shutil.copy2(src, dest)
            files[key] = f"~/.applypilot/files/{dest.name}"
            console.print(f"[green]Copied to {dest}[/green]")
            break

    # Free-form: certificates / other documents
    while Confirm.ask("Add another document (certificate, portfolio, etc.)?", default=False):
        doc_label = Prompt.ask("Short label for this document (e.g. 'aws_cert', 'portfolio')")
        key = doc_label.strip().lower().replace(" ", "_")
        while True:
            path_str = Prompt.ask("File path")
            src = Path(path_str.strip().strip('"').strip("'")).expanduser().resolve()
            if not src.exists():
                console.print(f"[red]File not found:[/red] {src}")
                continue
            dest = FILES_DIR / f"{key}{src.suffix.lower()}"
            shutil.copy2(src, dest)
            files[key] = f"~/.applypilot/files/{dest.name}"
            console.print(f"[green]Copied to {dest}[/green]")
            break

    if files:
        profile["files"] = files
        _write_profile_json(profile)
        if canonical_resume is not None:
            meta = canonical_resume.setdefault("meta", {})
            applypilot = meta.setdefault("applypilot", {})
            applypilot["files"] = files
            _write_resume_json(canonical_resume)
            console.print("[green]Document paths saved to profile.json and resume.json[/green]")
        else:
            console.print("[green]Document paths saved to profile.json[/green]")
