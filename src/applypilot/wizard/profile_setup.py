"""Profile Setup."""

from __future__ import annotations

import json

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from applypilot.config import (
    PROFILE_PATH,
    SEARCH_CONFIG_PATH,
)

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


from applypilot.wizard.prompts import _prompt_compensation, _prompt_target_locations


def _write_profile_json(profile: dict) -> None:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")


def _setup_tailoring_config(target_role: str) -> dict:
    """Collect lightweight tailoring preferences for the validation stack."""

    console.print("\n[bold cyan]Tailoring Preferences[/bold cyan]")
    primary_role_type = (
            Prompt.ask(
                "Primary role type key",
                default="software_engineer" if "engineer" in target_role.lower() else "general",
            ).strip()
            or "general"
    )
    min_bullets = Prompt.ask("Minimum bullets per role", default="2").strip()
    max_bullets = Prompt.ask("Maximum bullets per role", default="5").strip()
    min_metrics_ratio = Prompt.ask("Minimum metrics ratio (0-1)", default="0.7").strip()

    return {
        "default_role_type": primary_role_type,
        "validation": {
            "enabled": True,
            "max_retries": 3,
            "min_bullets_per_role": int(min_bullets or "2"),
            "max_bullets_per_role": int(max_bullets or "5"),
            "min_metrics_ratio": float(min_metrics_ratio or "0.7"),
        },
        "role_types": {},
    }


def _setup_profile() -> dict:
    """Walk through profile questions and return a nested profile dict."""
    console.print(
        Panel(
            "[bold]Step 2: Profile[/bold]\nTell ApplyPilot about yourself. This powers scoring, tailoring, and auto-fill."
        )
    )

    profile: dict = {}

    # -- Personal --
    console.print("\n[bold cyan]Personal Information[/bold cyan]")
    profile["personal"] = {
        "full_name": Prompt.ask("Full name"),
        "email": Prompt.ask("Email address"),
        "phone": Prompt.ask("Phone number", default=""),
        "city": Prompt.ask("City"),
        "country": Prompt.ask("Country"),
        "linkedin_url": Prompt.ask("LinkedIn URL", default=""),
        "password": Prompt.ask("Job site password (used for login walls during auto-apply)", password=True, default=""),
    }
    if profile["personal"]["password"]:
        console.print(
            "[yellow]Note: Password is stored in plaintext in profile.json. "
            "Consider setting APPLYPILOT_SITE_PASSWORD instead.[/yellow]"
        )

    # -- Work Authorization --
    console.print("\n[bold cyan]Work Authorization[/bold cyan]")
    profile["work_authorization"] = {
        "legally_authorized": Confirm.ask("Are you legally authorized to work in your target country?"),
        "needs_sponsorship": Confirm.ask("Will you now or in the future need sponsorship?"),
    }

    # -- Compensation --
    console.print("\n[bold cyan]Compensation[/bold cyan]")
    profile["compensation"] = _prompt_compensation({})

    # -- Target Locations --
    from applypilot.salary import clean_number, to_usd

    comp = profile["compensation"]
    cur_salary = clean_number(comp.get("current_salary", ""))
    cur_currency = comp.get("salary_currency", "USD")
    salary_usd = to_usd(clean_number(comp.get("salary_expectation", "")), cur_currency)
    profile["target_locations"] = _prompt_target_locations(salary_usd, cur_salary, cur_currency)

    # -- Experience --
    console.print("\n[bold cyan]Experience[/bold cyan]")
    profile["experience"] = {
        "years_of_experience_total": Prompt.ask("Years of professional experience", default=""),
        "education_level": Prompt.ask("Highest education (e.g. Bachelor's, Master's, PhD, Self-taught)", default=""),
        "current_title": Prompt.ask("Current/most recent job title", default=""),
    }
    from applypilot.wizard.resume_setup import _collect_education  # noqa: E402 — lazy to avoid circular

    profile["education"] = _collect_education()
    target_role = profile["experience"]["current_title"]
    profile["tailoring_config"] = _setup_tailoring_config(target_role)

    # -- Skills Boundary --
    console.print("\n[bold cyan]Skills[/bold cyan] (comma-separated)")
    langs = Prompt.ask("Programming languages", default="")
    frameworks = Prompt.ask("Frameworks & libraries", default="")
    tools = Prompt.ask("Tools & platforms (e.g. Docker, AWS, Git)", default="")
    profile["skills"] = [
        {"name": "Programming Languages", "keywords": [s.strip() for s in langs.split(",") if s.strip()]},
        {"name": "Frameworks & Libraries", "keywords": [s.strip() for s in frameworks.split(",") if s.strip()]},
        {"name": "Tools & Platforms", "keywords": [s.strip() for s in tools.split(",") if s.strip()]},
    ]

    # -- EEO Voluntary (defaults) --
    profile["eeo_voluntary"] = {
        "gender": "Decline to self-identify",
        "ethnicity": "Decline to self-identify",
        "veteran_status": "Decline to self-identify",
        "disability_status": "Decline to self-identify",
    }

    # -- Availability --
    profile["availability"] = {
        "earliest_start_date": Prompt.ask("Earliest start date", default="Immediately"),
    }

    # Save
    _write_profile_json(profile)
    console.print(f"\n[green]Profile saved to {PROFILE_PATH}[/green]")
    return profile


def _setup_searches() -> None:
    """Generate a searches.yaml from user input."""
    console.print(Panel("[bold]Step 3: Job Search Config[/bold]\nDefine what you're looking for."))

    location = Prompt.ask("Target location (e.g. 'Remote', 'Canada', 'New York, NY')", default="Remote")
    distance_str = Prompt.ask(
        "Search radius in miles for supported sources (JobSpy + URL templates), 0 for remote-only",
        default="0",
    )
    try:
        distance = int(distance_str)
    except ValueError:
        distance = 0

    roles_raw = Prompt.ask("Target job titles (comma-separated, e.g. 'Backend Engineer, Full Stack Developer')")
    roles = [r.strip() for r in roles_raw.split(",") if r.strip()]

    if not roles:
        console.print("[yellow]No roles provided. Using a default set.[/yellow]")
        roles = ["Software Engineer"]

    # CHANGED: Always include worldwide (empty location) + user's specific location.
    # This ensures discovery finds jobs everywhere, not just one location.
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
        '  - location: ""',
        "    remote: false",
    ]
    if location:
        is_remote = "remote" in location.lower() or distance == 0
        lines.append(f'  - location: "{location}"')
        lines.append(f"    remote: {str(is_remote).lower()}")
    lines.append("")
    lines.append("queries:")
    for i, role in enumerate(roles):
        lines.append(f'  - query: "{role}"')
        lines.append(f"    tier: {min(i + 1, 3)}")

    SEARCH_CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print(f"[green]Search config saved to {SEARCH_CONFIG_PATH}[/green]")
