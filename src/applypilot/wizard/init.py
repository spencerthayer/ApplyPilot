"""ApplyPilot first-time setup wizard.

Interactive flow that creates ~/.applypilot/ with:
  - resume.txt (and optionally resume.pdf)
  - profile.json
  - searches.yaml
  - .env (LLM API keys and runtime settings)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from applypilot.config import (
    APP_DIR,
    ENV_PATH,
    PROFILE_PATH,
    RESUME_PATH,
    RESUME_PDF_PATH,
    SEARCH_CONFIG_PATH,
    ensure_dirs,
)
from applypilot.apply.backends import (
    AgentBackendError,
    get_backend,
    detect_backends,
    get_preferred_backend,
)

console = Console()


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------

def _setup_resume() -> None:
    """Prompt for resume file and copy into APP_DIR."""
    console.print(Panel("[bold]Step 1: Resume[/bold]\nPoint to your master resume file (.txt or .pdf)."))

    while True:
        path_str = Prompt.ask("Resume file path")
        src = Path(path_str.strip().strip('"').strip("'")).expanduser().resolve()

        if not src.exists():
            console.print(f"[red]File not found:[/red] {src}")
            continue

        suffix = src.suffix.lower()
        if suffix not in (".txt", ".pdf"):
            console.print("[red]Unsupported format.[/red] Provide a .txt or .pdf file.")
            continue

        if suffix == ".txt":
            shutil.copy2(src, RESUME_PATH)
            console.print(f"[green]Copied to {RESUME_PATH}[/green]")
        elif suffix == ".pdf":
            shutil.copy2(src, RESUME_PDF_PATH)
            console.print(f"[green]Copied to {RESUME_PDF_PATH}[/green]")

            # Also ask for a plain-text version for LLM consumption
            txt_path_str = Prompt.ask(
                "Plain-text version of your resume (.txt)",
                default="",
            )
            if txt_path_str.strip():
                txt_src = Path(txt_path_str.strip().strip('"').strip("'")).expanduser().resolve()
                if txt_src.exists():
                    shutil.copy2(txt_src, RESUME_PATH)
                    console.print(f"[green]Copied to {RESUME_PATH}[/green]")
                else:
                    console.print("[yellow]File not found, skipping plain-text copy.[/yellow]")
        break


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def _setup_profile() -> dict:
    """Walk through profile questions and return a nested profile dict."""
    console.print(Panel("[bold]Step 2: Profile[/bold]\nTell ApplyPilot about yourself. This powers scoring, tailoring, and auto-fill."))

    profile: dict = {}

    # -- Personal --
    console.print("\n[bold cyan]Personal Information[/bold cyan]")
    full_name = Prompt.ask("Full name")
    profile["personal"] = {
        "full_name": full_name,
        "preferred_name": Prompt.ask("Preferred/nickname (leave blank to use first name)", default=""),
        "email": Prompt.ask("Email address"),
        "phone": Prompt.ask("Phone number", default=""),
        "city": Prompt.ask("City"),
        "province_state": Prompt.ask("Province/State (e.g. Ontario, California)", default=""),
        "country": Prompt.ask("Country"),
        "postal_code": Prompt.ask("Postal/ZIP code", default=""),
        "address": Prompt.ask("Street address (optional, used for form auto-fill)", default=""),
        "linkedin_url": Prompt.ask("LinkedIn URL", default=""),
        "github_url": Prompt.ask("GitHub URL (optional)", default=""),
        "portfolio_url": Prompt.ask("Portfolio URL (optional)", default=""),
        "website_url": Prompt.ask("Personal website URL (optional)", default=""),
        "password": Prompt.ask("Job site password (used for login walls during auto-apply)", password=True, default=""),
    }

    # -- Work Authorization --
    console.print("\n[bold cyan]Work Authorization[/bold cyan]")
    profile["work_authorization"] = {
        "legally_authorized_to_work": Confirm.ask("Are you legally authorized to work in your target country?"),
        "require_sponsorship": Confirm.ask("Will you now or in the future need sponsorship?"),
        "work_permit_type": Prompt.ask("Work permit type (e.g. Citizen, PR, Open Work Permit — leave blank if N/A)", default=""),
    }

    # -- Compensation --
    console.print("\n[bold cyan]Compensation[/bold cyan]")
    salary = Prompt.ask("Expected annual salary (number)", default="")
    salary_currency = Prompt.ask("Currency", default="USD")
    salary_range = Prompt.ask("Acceptable range (e.g. 80000-120000)", default="")
    range_parts = salary_range.split("-") if "-" in salary_range else [salary, salary]
    profile["compensation"] = {
        "salary_expectation": salary,
        "salary_currency": salary_currency,
        "salary_range_min": range_parts[0].strip(),
        "salary_range_max": range_parts[1].strip() if len(range_parts) > 1 else range_parts[0].strip(),
    }

    # -- Experience --
    console.print("\n[bold cyan]Experience[/bold cyan]")
    current_title = Prompt.ask("Current/most recent job title", default="")
    target_role = Prompt.ask("Target role (what you're applying for, e.g. 'Senior Backend Engineer')", default=current_title)
    profile["experience"] = {
        "years_of_experience_total": Prompt.ask("Years of professional experience", default=""),
        "education_level": Prompt.ask("Highest education (e.g. Bachelor's, Master's, PhD, Self-taught)", default=""),
        "current_title": current_title,
        "target_role": target_role,
    }

    # -- Education Array --
    profile["education"] = _collect_education()

    # -- Tailoring Config --
    profile["tailoring_config"] = _setup_tailoring_config(target_role)

    # -- Skills Boundary --
    console.print("\n[bold cyan]Skills[/bold cyan] (comma-separated)")
    langs = Prompt.ask("Programming languages", default="")
    frameworks = Prompt.ask("Frameworks & libraries", default="")
    tools = Prompt.ask("Tools & platforms (e.g. Docker, AWS, Git)", default="")
    profile["skills_boundary"] = {
        "programming_languages": [s.strip() for s in langs.split(",") if s.strip()],
        "frameworks": [s.strip() for s in frameworks.split(",") if s.strip()],
        "tools": [s.strip() for s in tools.split(",") if s.strip()],
    }

    # -- Resume Facts (preserved truths for tailoring) --
    console.print("\n[bold cyan]Resume Facts[/bold cyan]")
    console.print("[dim]These are preserved exactly during resume tailoring — the AI will never change them.[/dim]")
    companies = Prompt.ask("Companies to always keep (comma-separated)", default="")
    projects = Prompt.ask("Projects to always keep (comma-separated)", default="")
    school = Prompt.ask("School name(s) to preserve", default="")
    metrics = Prompt.ask("Real metrics to preserve (e.g. '99.9% uptime, 50k users')", default="")
    profile["resume_facts"] = {
        "preserved_companies": [s.strip() for s in companies.split(",") if s.strip()],
        "preserved_projects": [s.strip() for s in projects.split(",") if s.strip()],
        "preserved_school": school.strip(),
        "real_metrics": [s.strip() for s in metrics.split(",") if s.strip()],
    }

    # -- EEO Voluntary (defaults) --
    profile["eeo_voluntary"] = {
        "gender": "Decline to self-identify",
        "race_ethnicity": "Decline to self-identify",
        "veteran_status": "Decline to self-identify",
        "disability_status": "Decline to self-identify",
    }

    # -- Availability --
    profile["availability"] = {
        "earliest_start_date": Prompt.ask("Earliest start date", default="Immediately"),
    }

    # Save
    PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"\n[green]Profile saved to {PROFILE_PATH}[/green]")
    return profile


# ---------------------------------------------------------------------------
# Search config
# ---------------------------------------------------------------------------

def _setup_searches() -> None:
    """Generate a searches.yaml from user input."""
    console.print(Panel("[bold]Step 3: Job Search Config[/bold]\nDefine what you're looking for."))

    location = Prompt.ask("Target location (e.g. 'Remote', 'Canada', 'New York, NY')", default="Remote")
    distance_str = Prompt.ask("Search radius in miles (0 for remote-only)", default="0")
    try:
        distance = int(distance_str)
    except ValueError:
        distance = 0

    roles_raw = Prompt.ask(
        "Target job titles (comma-separated, e.g. 'Backend Engineer, Full Stack Developer')"
    )
    roles = [r.strip() for r in roles_raw.split(",") if r.strip()]

    if not roles:
        console.print("[yellow]No roles provided. Using a default set.[/yellow]")
        roles = ["Software Engineer"]

    # Build YAML content
    lines = [
        "# ApplyPilot search configuration",
        "# Edit this file to refine your job search queries.",
        "",
        "defaults:",
        f'  location: "{location}"',
        f"  distance: {distance}",
        "  hours_old: 72",
        "  results_per_site: 50",
        "",
        "locations:",
        f'  - location: "{location}"',
        f"    remote: {str(distance == 0).lower()}",
        "",
        "queries:",
    ]
    for i, role in enumerate(roles):
        lines.append(f'  - query: "{role}"')
        lines.append(f"    tier: {min(i + 1, 3)}")

    SEARCH_CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print(f"[green]Search config saved to {SEARCH_CONFIG_PATH}[/green]")


# ---------------------------------------------------------------------------
# AI Features
# ---------------------------------------------------------------------------

def _setup_ai_features() -> None:
    """Ask about AI scoring/tailoring — optional LLM configuration."""
    console.print(Panel(
        "[bold]Step 4: AI Features (optional)[/bold]\n"
        "An LLM powers job scoring, resume tailoring, and cover letters.\n"
        "Without this, you can still discover and enrich jobs."
    ))

    if not Confirm.ask("Enable AI scoring and resume tailoring?", default=True):
        console.print("[dim]Discovery-only mode. You can configure AI later with [bold]applypilot init[/bold].[/dim]")
        return

    console.print(
        "Supported providers: [bold]Gemini[/bold] (recommended, free tier), "
        "OpenAI, Claude, local (Ollama/llama.cpp)."
    )
    console.print("[dim]Enter any credentials you want to save now. Leave blank to skip each field.[/dim]")

    env_lines = ["# ApplyPilot configuration", ""]
    configured_sources: list[str] = []

    gemini_key = Prompt.ask("Gemini API key (optional, from aistudio.google.com)", default="").strip()
    if gemini_key:
        env_lines.append(f"GEMINI_API_KEY={gemini_key}")
        configured_sources.append("gemini")

    openai_key = Prompt.ask("OpenAI API key (optional)", default="").strip()
    if openai_key:
        env_lines.append(f"OPENAI_API_KEY={openai_key}")
        configured_sources.append("openai")

    anthropic_key = Prompt.ask("Anthropic API key (optional)", default="").strip()
    if anthropic_key:
        env_lines.append(f"ANTHROPIC_API_KEY={anthropic_key}")
        configured_sources.append("anthropic")

    local_url = Prompt.ask("Local LLM endpoint URL (optional)", default="").strip()
    if local_url:
        env_lines.append(f"LLM_URL={local_url}")
        configured_sources.append("local")

    if not configured_sources:
        console.print("[dim]No AI provider configured. You can add one later with [bold]applypilot init[/bold].[/dim]")
        return

    default_model_by_source = {
        "gemini": "gemini/gemini-3.0-flash",
        "openai": "openai/gpt-4o-mini",
        "anthropic": "anthropic/claude-3-5-haiku-latest",
        "local": "openai/local-model",
    }
    default_model = default_model_by_source.get(configured_sources[0], "openai/gpt-4o-mini")
    model = Prompt.ask(
        "LLM model (required, include provider prefix)",
        default=default_model,
    ).strip()
    env_lines.append(f"LLM_MODEL={model}")

    env_lines.append("")
    ENV_PATH.write_text("\n".join(env_lines), encoding="utf-8")
    if len(configured_sources) > 1:
        configured = ", ".join(configured_sources)
        console.print(
            f"[yellow]Multiple LLM providers saved ({configured}). "
            "Runtime routing follows LLM_MODEL's provider prefix.[/yellow]"
        )
    console.print(f"[green]AI configuration saved to {ENV_PATH}[/green]")


# ---------------------------------------------------------------------------
# Auto-Apply
# ---------------------------------------------------------------------------

def _setup_auto_apply() -> None:
    """Configure autonomous job application (requires Claude Code CLI)."""
    console.print(Panel(
        "[bold]Step 5: Auto-Apply (optional)[/bold]\n"
        "ApplyPilot can autonomously fill and submit job applications\n"
        "using Claude Code as the browser agent."
    ))

    if not Confirm.ask("Enable autonomous job applications?", default=True):
        console.print("[dim]You can apply manually using the tailored resumes ApplyPilot generates.[/dim]")
        return

    # Check for Claude Code CLI
    if shutil.which("claude"):
        console.print("[green]Claude Code CLI detected.[/green]")
    else:
        console.print(
            "[yellow]Claude Code CLI not found on PATH.[/yellow]\n"
            "Install it from: [bold]https://claude.ai/code[/bold]\n"
            "Auto-apply won't work until Claude Code is installed."
        )

    # Optional: CapSolver for CAPTCHAs
    console.print("\n[dim]Some job sites use CAPTCHAs. CapSolver can handle them automatically.[/dim]")
    if Confirm.ask("Configure CapSolver API key? (optional)", default=False):
        capsolver_key = Prompt.ask("CapSolver API key")
        # Append to existing .env or create
        if ENV_PATH.exists():
            existing = ENV_PATH.read_text(encoding="utf-8")
            if "CAPSOLVER_API_KEY" not in existing:
                ENV_PATH.write_text(
                    existing.rstrip() + f"\nCAPSOLVER_API_KEY={capsolver_key}\n",
                    encoding="utf-8",
                )
        else:
            ENV_PATH.write_text(f"# ApplyPilot configuration\nCAPSOLVER_API_KEY={capsolver_key}\n", encoding="utf-8")
        console.print("[green]CapSolver key saved.[/green]")
    else:
        console.print("[dim]Skipped. Add CAPSOLVER_API_KEY to .env later if needed.[/dim]")


# ---------------------------------------------------------------------------
# Agent Configuration
# ---------------------------------------------------------------------------

def _setup_agents() -> None:
    """Configure AI agent backends using the unified AgentBackend abstraction."""
    console.print(Panel(
        "[bold]Step 6: Agent Configuration (optional)[/bold]\n"
        "ApplyPilot can use multiple AI agent backends for auto-apply.\n"
        "OpenCode is the preferred backend with MCP server support."
    ))

    # Detect available backends
    available_backends = detect_backends()
    
    if not available_backends:
        console.print("[yellow]No agent backends found on PATH.[/yellow]")
        console.print(
            "Install OpenCode from: [bold]https://opencode.ai[/bold]\n"
            "Or Claude Code from: [bold]https://claude.ai/code[/bold]\n"
            "Then re-run [bold]applypilot init[/bold] to configure MCP servers."
        )
        return

    # Get preferred backend (opencode > claude)
    preferred = get_preferred_backend()
    console.print(f"[green]Detected backends: {', '.join(available_backends)}[/green]")
    console.print(f"[green]Preferred backend: {preferred}[/green]")

    # Get the backend instance
    try:
        backend = get_backend(preferred)
    except AgentBackendError as e:
        console.print(f"[red]Failed to initialize backend: {e}[/red]")
        return

    # Setup the backend (configures MCP servers)
    console.print(f"[dim]Setting up {preferred} backend...[/dim]")
    
    # For OpenCode, offer to import from Claude
    import_from = None
    if preferred == "opencode" and "claude" in available_backends:
        if Confirm.ask(
            "Import MCP servers from Claude Code config?",
            default=True,
        ):
            import_from = "claude"
    
    result = backend.setup(import_from=import_from)
    
    if result["success"]:
        console.print(
            f"[green]{preferred.title()} backend configured successfully![/green]"
        )
        if result["servers_added"]:
            console.print(
                f"[green]Added {len(result['servers_added'])} MCP server(s):[/green] {', '.join(result['servers_added'])}"
            )
        if result["servers_existing"]:
            console.print(
                f"[dim]Already registered:[/dim] {', '.join(result['servers_existing'])}"
            )
    else:
        console.print("[yellow]Some issues occurred during setup:[/yellow]")
        for error in result["errors"]:
            console.print(f"  [red]-[/red] {error}")
    
    # Verify required servers
    try:
        existing = set(backend.list_mcp_servers())
        required_servers = ["playwright", "gmail"]
        missing = [s for s in required_servers if s not in existing]
        
        if not missing:
            console.print("[green]✓ All required MCP servers are ready![/green]")
        else:
            console.print(
                f"[yellow]⚠ Missing MCP servers:[/yellow] {', '.join(missing)}"
            )
            console.print(
                "[dim]Run manually: opencode mcp add <name> -- <command>[/dim]"
            )
    except AgentBackendError as e:
        console.print(f"[yellow]Could not verify MCP servers: {e}[/yellow]")


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def run_wizard() -> None:
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

    # Step 1: Resume
    _setup_resume()
    console.print()

    # Step 2: Profile
    _setup_profile()
    console.print()

    # Step 3: Search config
    _setup_searches()
    console.print()

    # Step 4: AI features (optional LLM)
    _setup_ai_features()
    console.print()

    # Step 5: Auto-apply (Claude Code detection)
    _setup_auto_apply()
    console.print()

    # Step 6: Agent Configuration (OpenCode MCP setup)
    _setup_agents()
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
        unlock_hint = "\n[dim]To unlock Tier 2: configure an LLM API key (re-run [bold]applypilot init[/bold]).[/dim]"
    elif tier == 2:
        unlock_hint = "\n[dim]To unlock Tier 3: install Claude Code CLI + Chrome.[/dim]"

    console.print(
        Panel.fit(
            "[bold green]Setup complete![/bold green]\n\n"
            f"[bold]Your tier: Tier {tier} — {TIER_LABELS[tier]}[/bold]\n\n"
            + "\n".join(tier_lines)
            + unlock_hint,
            border_style="green",
        )
    )



# ---------------------------------------------------------------------------
# Education collection
# ---------------------------------------------------------------------------

def _collect_education() -> list:
    """Collect education entries as an array."""
    console.print("\n[bold cyan]Education Details[/bold cyan]")
    education = []
    
    while True:
        if education:
            if not Confirm.ask("Add another education entry?", default=False):
                break
        else:
            if not Confirm.ask("Would you like to add education details?", default=True):
                break
        
        console.print(f"\n[dim]Education entry {len(education) + 1}[/dim]")
        entry = {
            "institution": Prompt.ask("Institution name (e.g. 'Georgia Institute of Technology')", default=""),
            "degree": Prompt.ask("Degree (e.g. 'Bachelor of Science', 'Master of Arts')", default=""),
            "field": Prompt.ask("Field of study (e.g. 'Computer Science', 'Business Administration')", default=""),
            "dates": Prompt.ask("Dates attended (e.g. '2010-2014', '2015-2017')", default=""),
        }
        
        # Optional GPA
        gpa = Prompt.ask("GPA (optional, leave blank to skip)", default="")
        if gpa.strip():
            entry["gpa"] = gpa.strip()
        
        education.append(entry)
        console.print(f"[green]Added education entry for {entry['institution']}[/green]")
    
    return education


# ---------------------------------------------------------------------------
# Tailoring config setup
# ---------------------------------------------------------------------------

def _setup_tailoring_config(target_role: str) -> dict:
    """Set up basic tailoring configuration for config-driven system."""
    console.print("\n[bold cyan]Tailoring Configuration[/bold cyan]")
    console.print("[dim]ApplyPilot can tailor your resume per job type using a config-driven approach.[/dim]")
    
    if not Confirm.ask("Enable config-driven tailoring?", default=True):
        return {}
    
    tailoring_config = {
        "enabled": True,
        "role_types": {
            "general": {
                "label": "General",
                "detection_keywords": [],
                "positioning_frame": "Professional",
                "title_variants": [],
                "example_resumes": [],
                "instructions": {
                    "bullet_template": "CAR",
                    "skills_order": [],
                    "max_bullets_per_role": 6,
                    "emphasis": []
                },
                "constraints": {
                    "banned_phrases": ["expert", "ninja", "guru"],
                    "required_patterns": [],
                    "mechanism_required": False
                },
                "guidelines": {
                    "summary": "3-4 lines. Focus on relevant experience and skills.",
                    "bullets": "CAR format: Action + Context + Result",
                    "skills": "Group by relevance to role"
                }
            }
        },
        "global_rules": {
            "max_summary_lines": 4,
            "selected_impact_metrics": 5,
            "role_compression": {
                "enabled": True,
                "older_than_years": 10,
                "max_bullets_per_old_role": 3
            },
            "formatting": {
                "date_format": "YYYY-MM",
                "bullet_style": "sentence_case",
                "skills_separator": " | "
            }
        },
        "quality_gates": {
            "step_1_normalize": {
                "enabled": True,
                "min_confidence": 0.8,
                "required_fields": ["role_type", "core_outcomes", "hard_requirements"]
            },
            "step_2_frame": {
                "enabled": True,
                "min_confidence": 0.9
            },
            "step_6_bullets": {
                "enabled": True,
                "template_compliance": 0.85,
                "banned_phrases_check": True,
                "mechanism_required_for": ["ai", "system", "platform", "architecture"]
            },
            "step_9_credibility": {
                "enabled": True,
                "min_evidence_coverage": 0.9
            }
        },
        "evidence_ledger": {
            "enabled": True,
            "track_metrics": True,
            "track_sources": True,
            "output_format": "markdown"
        }
    }
    
    # Ask if user wants to add a specific role type based on their target
    target_lower = target_role.lower()
    if any(kw in target_lower for kw in ["engineer", "developer", "programmer", "software"]):
        console.print("\n[dim]Detected software engineering role. Adding Software Engineer role type.[/dim]")
        tailoring_config["role_types"]["software_engineer"] = {
            "label": "Software Engineer",
            "detection_keywords": ["engineer", "developer", "programmer", "software"],
            "positioning_frame": "Technical Builder",
            "title_variants": ["Software Engineer", "Full-Stack Engineer", "Backend Engineer"],
            "example_resumes": [],
            "instructions": {
                "summary_focus": "Lead with systems built and technical depth",
                "bullet_template": "CAR",
                "skills_order": ["languages", "systems", "databases", "tools"],
                "max_bullets_per_role": 6,
                "emphasis": ["architecture", "reliability", "scale"]
            },
            "constraints": {
                "banned_phrases": ["expert", "ninja", "guru"],
                "required_patterns": ["built", "designed", "implemented", "architected"],
                "mechanism_required": True
            },
            "guidelines": {
                "summary": "3-4 lines. Mechanism + Outcome. No adjective stacks.",
                "bullets": "CAR format: Action + System/Method + Measurable Result",
                "skills": "Group by theme. No proficiency labels. Lead with role-relevant."
            }
        }
    elif any(kw in target_lower for kw in ["product manager", "product owner", "pm"]):
        console.print("\n[dim]Detected product management role. Adding Product Manager role type.[/dim]")
        tailoring_config["role_types"]["product_manager"] = {
            "label": "Product Manager",
            "detection_keywords": ["product manager", "product owner", "pm"],
            "positioning_frame": "Product Leader",
            "title_variants": ["Product Manager", "Senior PM", "Director of Product"],
            "example_resumes": [],
            "instructions": {
                "summary_focus": "Lead with outcomes and decision-making",
                "bullet_template": "WHO",
                "skills_order": ["product", "growth", "technical", "leadership"],
                "max_bullets_per_role": 6,
                "emphasis": ["strategy", "execution", "stakeholder_management"]
            },
            "constraints": {
                "banned_phrases": ["visionary", "thought leader", "rockstar"],
                "required_patterns": ["led", "drove", "delivered", "achieved"],
                "mechanism_required": False
            },
            "guidelines": {
                "summary": "3-4 lines. Scope + Decision + Business Outcome.",
                "bullets": "WHO format: Action + Scope/Decision + Result",
                "skills": "Lead with product competencies. Technical as credibility."
            }
        }
    
    console.print(f"[green]Tailoring configuration created with {len(tailoring_config['role_types'])} role type(s)[/green]")
    console.print("[dim]You can customize this further by editing profile.json[/dim]")
    
    return tailoring_config