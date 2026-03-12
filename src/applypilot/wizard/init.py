"""ApplyPilot first-time setup wizard.

Interactive flow that creates ~/.applypilot/ with:
  - profile.json (authoritative ApplyPilot profile/settings)
  - resume.json (canonical JSON Resume artifact)
  - resume.txt (legacy plain-text fallback, optional)
  - searches.yaml
  - .env (LLM provider config)
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from applypilot.config import (
    APP_DIR,
    AUTO_APPLY_AGENT_CHOICES,
    DEFAULT_AUTO_APPLY_AGENT,
    ENV_PATH,
    FILES_DIR,
    PROFILE_PATH,
    RESUME_JSON_PATH,
    RESUME_PATH,
    RESUME_PDF_PATH,
    SEARCH_CONFIG_PATH,
    ensure_dirs,
)
from applypilot.llm_provider import LLM_PROVIDER_SPECS, WIZARD_PROVIDER_ORDER
from applypilot.resume_json import (
    DEFAULT_RENDER_THEME,
    load_resume_json_from_path,
    normalize_legacy_profile,
    normalize_profile_from_resume_json,
)

console = Console()

_PROVIDER_CREDENTIAL_PROMPTS = {
    "gemini": "Gemini API key (from aistudio.google.com)",
    "openrouter": "OpenRouter API key (from openrouter.ai/keys)",
    "openai": "OpenAI API key",
    "anthropic": "Anthropic API key",
    "local": "Local LLM endpoint URL",
}

_PROVIDER_MODEL_PROMPTS = {
    "gemini": "Model",
    "openrouter": "Model",
    "openai": "Model",
    "anthropic": "Model",
    "local": "Model name",
}


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


def _write_resume_json(data: dict) -> None:
    RESUME_JSON_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_profile_json(profile: dict) -> None:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")


def _copy_resume_json(src_path: Path) -> dict:
    data = load_resume_json_from_path(src_path)
    if src_path.resolve() != RESUME_JSON_PATH.resolve():
        shutil.copy2(src_path, RESUME_JSON_PATH)
        console.print(f"[green]Copied canonical resume to {RESUME_JSON_PATH}[/green]")
    else:
        console.print(f"[green]Using canonical resume at {RESUME_JSON_PATH}[/green]")
    return data


def _profile_for_canonical_resume(resume_data: dict) -> dict:
    """Return the authoritative profile for a canonical resume setup."""

    if PROFILE_PATH.exists():
        try:
            return normalize_legacy_profile(json.loads(PROFILE_PATH.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass

    profile = normalize_profile_from_resume_json(resume_data)
    _write_profile_json(profile)
    return profile


def _legacy_profile_to_resume_json(profile: dict, resume_text: str = "") -> dict:
    from applypilot.scoring.pdf import parse_entries, parse_resume, parse_skills

    parsed_resume = parse_resume(resume_text) if resume_text.strip() else {"sections": {}, "name": "", "title": "", "contact": ""}
    sections = parsed_resume.get("sections", {})

    personal = profile.get("personal", {})
    experience = profile.get("experience", {})
    work_history = profile.get("work_history", [])
    education = profile.get("education", [])
    skills_boundary = profile.get("skills_boundary", {})
    resume_facts = profile.get("resume_facts", {})

    basics = {
        "name": personal.get("full_name", "") or parsed_resume.get("name", ""),
        "label": experience.get("target_role", "") or experience.get("current_title", "") or parsed_resume.get("title", ""),
        "email": personal.get("email", ""),
        "phone": personal.get("phone", ""),
        "url": personal.get("website_url", ""),
        "summary": sections.get("SUMMARY", "").strip(),
        "location": {
            "address": personal.get("address", ""),
            "city": personal.get("city", ""),
            "region": personal.get("province_state", ""),
            "countryCode": personal.get("country", ""),
            "postalCode": personal.get("postal_code", ""),
        },
        "profiles": [],
    }

    if personal.get("linkedin_url"):
        basics["profiles"].append({"network": "LinkedIn", "url": personal["linkedin_url"]})
    if personal.get("github_url"):
        basics["profiles"].append({"network": "GitHub", "url": personal["github_url"]})
    if personal.get("portfolio_url"):
        basics["profiles"].append({"network": "Portfolio", "url": personal["portfolio_url"]})

    parsed_experience = parse_entries(sections.get("EXPERIENCE", "")) if sections.get("EXPERIENCE") else []
    work: list[dict] = []
    if work_history:
        for index, role in enumerate(work_history):
            parsed_entry = parsed_experience[index] if index < len(parsed_experience) else {}
            highlights = parsed_entry.get("bullets", []) if parsed_entry else role.get("highlights", [])
            work.append(
                {
                    "name": role.get("company", ""),
                    "position": role.get("position", ""),
                    "location": role.get("location", ""),
                    "startDate": str(role.get("start_year") or role.get("start_date") or ""),
                    "endDate": str(role.get("end_year") or role.get("end_date") or ""),
                    "summary": role.get("summary", ""),
                    "highlights": highlights,
                    "x-applypilot": {
                        "key_metrics": role.get("key_metrics", []),
                    },
                }
            )
    else:
        for entry in parsed_experience:
            title = entry.get("title", "")
            parts = [part.strip() for part in title.split("|")]
            position = parts[0] if parts else title
            company = parts[1] if len(parts) > 1 else ""
            date_value = parts[2] if len(parts) > 2 else ""
            work.append(
                {
                    "name": company,
                    "position": position,
                    "location": entry.get("subtitle", ""),
                    "startDate": date_value,
                    "summary": "",
                    "highlights": entry.get("bullets", []),
                    "x-applypilot": {"key_metrics": []},
                }
            )

    parsed_projects = parse_entries(sections.get("PROJECTS", "")) if sections.get("PROJECTS") else []
    projects = []
    for entry in parsed_projects:
        projects.append(
            {
                "name": entry.get("title", ""),
                "description": entry.get("subtitle", ""),
                "highlights": entry.get("bullets", []),
            }
        )

    canonical_skills = []
    if sections.get("TECHNICAL SKILLS"):
        for label, value in parse_skills(sections["TECHNICAL SKILLS"]):
            canonical_skills.append({"name": label, "keywords": [s.strip() for s in value.split(",") if s.strip()]})
    elif skills_boundary:
        label_map = {
            "programming_languages": "Programming Languages",
            "frameworks": "Frameworks & Libraries",
            "devops": "DevOps & Infra",
            "databases": "Databases",
            "tools": "Tools & Platforms",
        }
        for key, values in skills_boundary.items():
            canonical_skills.append({"name": label_map.get(key, key.replace("_", " ").title()), "keywords": values})

    canonical_education = []
    for entry in education:
        canonical_education.append(
            {
                "institution": entry.get("institution", ""),
                "studyType": entry.get("studyType", ""),
                "area": entry.get("area", ""),
                "endDate": entry.get("endDate", ""),
            }
        )

    applypilot_meta = {
        "personal": {
            "preferred_name": personal.get("preferred_name", ""),
            "address": personal.get("address", ""),
            "province_state": personal.get("province_state", ""),
            "country": personal.get("country", ""),
            "postal_code": personal.get("postal_code", ""),
            "portfolio_url": personal.get("portfolio_url", ""),
            "website_url": personal.get("website_url", ""),
            "github_url": personal.get("github_url", ""),
            "linkedin_url": personal.get("linkedin_url", ""),
        },
        "target_role": experience.get("target_role", "") or experience.get("current_title", ""),
        "years_of_experience_total": experience.get("years_of_experience_total", ""),
        "work_authorization": profile.get("work_authorization", {}),
        "compensation": profile.get("compensation", {}),
        "availability": profile.get("availability", {}),
        "eeo_voluntary": profile.get("eeo_voluntary", {}),
        "resume_facts": resume_facts,
        "tailoring_config": profile.get("tailoring_config", {}),
        "files": profile.get("files", {}),
        "render": {"theme": DEFAULT_RENDER_THEME},
    }

    return {
        "basics": basics,
        "work": work,
        "education": canonical_education,
        "skills": canonical_skills,
        "projects": projects,
        "meta": {
            "canonical": "https://jsonresume.org/schema",
            "version": "v1.0.0",
            "applypilot": applypilot_meta,
        },
    }


def _create_resume_json_scaffold() -> dict:
    console.print(Panel(
        "[bold]Step 1: Canonical Resume[/bold]\nCreate a starter [cyan]resume.json[/cyan] that follows JSON Resume."
    ))
    name = Prompt.ask("Full name")
    email = Prompt.ask("Email address")
    label = Prompt.ask("Professional title", default="")
    phone = Prompt.ask("Phone number", default="")
    city = Prompt.ask("City", default="")
    country = Prompt.ask("Country / country code", default="")
    return {
        "basics": {
            "name": name,
            "label": label,
            "email": email,
            "phone": phone,
            "summary": "",
            "location": {
                "city": city,
                "countryCode": country,
            },
            "profiles": [],
        },
        "work": [],
        "education": [],
        "skills": [],
        "projects": [],
        "meta": {
            "canonical": "https://jsonresume.org/schema",
            "version": "v1.0.0",
            "applypilot": {
                "render": {"theme": DEFAULT_RENDER_THEME},
            },
        },
    }


def _prompt_missing_applypilot_fields(resume_data: dict) -> dict:
    console.print(
        Panel(
            "[bold]Step 2: ApplyPilot Metadata[/bold]\n"
            "Fill any missing ApplyPilot-specific fields. Standard resume content stays in resume.json."
        )
    )

    basics = resume_data.setdefault("basics", {})
    location = basics.setdefault("location", {})
    meta = resume_data.setdefault("meta", {})
    applypilot = meta.setdefault("applypilot", {})
    personal = applypilot.setdefault("personal", {})
    work_auth = applypilot.setdefault("work_authorization", {})
    compensation = applypilot.setdefault("compensation", {})
    availability = applypilot.setdefault("availability", {})
    eeo = applypilot.setdefault("eeo_voluntary", {})
    resume_facts = applypilot.setdefault("resume_facts", {})
    applypilot.setdefault("files", {})
    derived_profile = normalize_profile_from_resume_json(resume_data)
    derived_personal = derived_profile.get("personal", {})

    if not personal.get("linkedin_url"):
        personal["linkedin_url"] = derived_personal.get("linkedin_url", "")
    if not personal.get("github_url"):
        personal["github_url"] = derived_personal.get("github_url", "")
    if not personal.get("portfolio_url"):
        personal["portfolio_url"] = derived_personal.get("portfolio_url", "")
    if not personal.get("website_url"):
        personal["website_url"] = derived_personal.get("website_url", "")

    if not basics.get("name"):
        basics["name"] = Prompt.ask("Full name")
    if not basics.get("email"):
        basics["email"] = Prompt.ask("Email address")
    basics["phone"] = basics.get("phone") or Prompt.ask("Phone number", default="")
    location["city"] = location.get("city") or Prompt.ask("City", default="")
    if not location.get("countryCode") and not personal.get("country"):
        location["countryCode"] = Prompt.ask("Country / country code", default="")

    if not personal.get("linkedin_url"):
        personal["linkedin_url"] = Prompt.ask("LinkedIn URL", default="")
    if not personal.get("github_url"):
        personal["github_url"] = Prompt.ask("GitHub URL", default="")

    if "legally_authorized_to_work" not in work_auth and "legally_authorized" not in work_auth:
        value = Confirm.ask("Are you legally authorized to work in your target country?")
        work_auth["legally_authorized_to_work"] = value
        work_auth["legally_authorized"] = value
    if "require_sponsorship" not in work_auth and "needs_sponsorship" not in work_auth:
        value = Confirm.ask("Will you now or in the future need sponsorship?")
        work_auth["require_sponsorship"] = value
        work_auth["needs_sponsorship"] = value
    work_auth.setdefault("work_permit_type", "")

    salary = str(compensation.get("salary_expectation", "")).strip()
    if not salary:
        salary = Prompt.ask("Expected annual salary (number)", default="")
    salary_currency = str(compensation.get("salary_currency", "")).strip() or Prompt.ask("Currency", default="USD")
    salary_range = (
        f"{compensation.get('salary_range_min', '')}-{compensation.get('salary_range_max', '')}".strip("-")
        if compensation.get("salary_range_min") or compensation.get("salary_range_max")
        else Prompt.ask("Acceptable range (e.g. 80000-120000)", default="")
    )
    clean_salary = re.sub(r"[$,\s]", "", salary)
    clean_range = re.sub(r"[$,\s]", "", salary_range)
    range_parts = clean_range.split("-") if "-" in clean_range else [clean_salary, clean_salary]
    compensation["salary_expectation"] = salary
    compensation["salary_currency"] = salary_currency or "USD"
    compensation["salary_range_min"] = range_parts[0].strip() if range_parts and range_parts[0] else ""
    compensation["salary_range_max"] = range_parts[1].strip() if len(range_parts) > 1 else compensation["salary_range_min"]
    compensation.setdefault("currency_conversion_note", "")

    if not applypilot.get("years_of_experience_total"):
        derived_years = derived_profile.get("experience", {}).get("years_of_experience_total", "")
        if derived_years:
            applypilot["years_of_experience_total"] = derived_years
        else:
            applypilot["years_of_experience_total"] = Prompt.ask("Years of professional experience", default="")
    if not applypilot.get("target_role"):
        default_role = derived_profile.get("experience", {}).get("target_role", "") or basics.get("label", "")
        applypilot["target_role"] = Prompt.ask("Target role", default=default_role)

    if "earliest_start_date" not in availability or availability.get("earliest_start_date") in (None, ""):
        availability["earliest_start_date"] = Prompt.ask("Earliest start date", default="Immediately")
    availability.setdefault("available_for_full_time", "Yes")
    availability.setdefault("available_for_contract", "No")

    eeo.setdefault("gender", "Decline to self-identify")
    eeo.setdefault("race_ethnicity", "Decline to self-identify")
    eeo.setdefault("ethnicity", eeo["race_ethnicity"])
    eeo.setdefault("veteran_status", "Decline to self-identify")
    eeo.setdefault("disability_status", "Decline to self-identify")

    resume_facts.setdefault("preserved_companies", [])
    resume_facts.setdefault("preserved_projects", [])
    resume_facts.setdefault("preserved_school", "")
    resume_facts.setdefault("real_metrics", [])

    if "tailoring_config" not in applypilot or not isinstance(applypilot.get("tailoring_config"), dict):
        applypilot["tailoring_config"] = _setup_tailoring_config(str(applypilot.get("target_role", "")))

    applypilot.setdefault("render", {"theme": DEFAULT_RENDER_THEME})
    return resume_data


def _setup_canonical_resume(resume_json: Path | None = None) -> tuple[dict, dict] | None:
    if RESUME_JSON_PATH.exists() and resume_json is None:
        console.print(f"[green]Using existing canonical resume:[/green] {RESUME_JSON_PATH}")
        data = load_resume_json_from_path(RESUME_JSON_PATH)
        data = _prompt_missing_applypilot_fields(data)
        _write_resume_json(data)
        return data, _profile_for_canonical_resume(data)

    if resume_json is not None:
        data = _copy_resume_json(resume_json)
        data = _prompt_missing_applypilot_fields(data)
        _write_resume_json(data)
        return data, _profile_for_canonical_resume(data)

    console.print(Panel(
        "[bold]Step 1: Resume Source[/bold]\n"
        "Choose how to create your canonical [cyan]resume.json[/cyan]."
    ))
    choice = Prompt.ask(
        "Resume setup mode",
        choices=["import", "migrate", "scaffold", "legacy"],
        default="import" if PROFILE_PATH.exists() or RESUME_PATH.exists() else "scaffold",
    )

    if choice == "legacy":
        return None
    if choice == "import":
        path_str = Prompt.ask("Path to JSON Resume file")
        data = _copy_resume_json(Path(path_str.strip().strip('"').strip("'")).expanduser().resolve())
    elif choice == "migrate":
        if not PROFILE_PATH.exists():
            console.print("[yellow]Legacy profile.json not found. Falling back to scaffold.[/yellow]")
            data = _create_resume_json_scaffold()
        else:
            profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            resume_text = RESUME_PATH.read_text(encoding="utf-8") if RESUME_PATH.exists() else ""
            data = _legacy_profile_to_resume_json(profile, resume_text)
        _write_resume_json(data)
        console.print(f"[green]Migrated legacy profile into {RESUME_JSON_PATH}[/green]")
    else:
        data = _create_resume_json_scaffold()
        _write_resume_json(data)
        console.print(f"[green]Created scaffold at {RESUME_JSON_PATH}[/green]")

    data = load_resume_json_from_path(RESUME_JSON_PATH)
    data = _prompt_missing_applypilot_fields(data)
    _write_resume_json(data)
    return data, _profile_for_canonical_resume(data)


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def _collect_education() -> list[dict]:
    """Collect structured education entries for the profile."""

    console.print("\n[bold cyan]Education[/bold cyan]")
    console.print("[dim]Press Enter on the school name to skip or finish this section.[/dim]")
    education: list[dict] = []
    while True:
        institution = Prompt.ask("School / institution", default="").strip()
        if not institution:
            break
        education.append(
            {
                "institution": institution,
                "studyType": Prompt.ask("Degree", default="").strip(),
                "area": Prompt.ask("Field of study", default="").strip(),
                "endDate": Prompt.ask("Graduation date (YYYY or YYYY-MM-DD)", default="").strip(),
            }
        )
        if not Confirm.ask("Add another education entry?", default=False):
            break
    return education


def _setup_tailoring_config(target_role: str) -> dict:
    """Collect lightweight tailoring preferences for the validation stack."""

    console.print("\n[bold cyan]Tailoring Preferences[/bold cyan]")
    primary_role_type = Prompt.ask(
        "Primary role type key",
        default="software_engineer" if "engineer" in target_role.lower() else "general",
    ).strip() or "general"
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
    console.print(Panel("[bold]Step 2: Profile[/bold]\nTell ApplyPilot about yourself. This powers scoring, tailoring, and auto-fill."))

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
    salary = Prompt.ask("Expected annual salary (number)", default="")
    salary_currency = Prompt.ask("Currency", default="USD")
    salary_range = Prompt.ask("Acceptable range (e.g. 80000-120000)", default="")
    clean_salary = re.sub(r"[$,\s]", "", salary)
    clean_range = re.sub(r"[$,\s]", "", salary_range)
    range_parts = clean_range.split("-") if "-" in clean_range else [clean_salary, clean_salary]
    profile["compensation"] = {
        "salary_expectation": salary,
        "salary_currency": salary_currency,
        "salary_range_min": range_parts[0].strip(),
        "salary_range_max": range_parts[1].strip() if len(range_parts) > 1 else range_parts[0].strip(),
    }

    # -- Experience --
    console.print("\n[bold cyan]Experience[/bold cyan]")
    profile["experience"] = {
        "years_of_experience_total": Prompt.ask("Years of professional experience", default=""),
        "education_level": Prompt.ask("Highest education (e.g. Bachelor's, Master's, PhD, Self-taught)", default=""),
        "current_title": Prompt.ask("Current/most recent job title", default=""),
    }
    profile["education"] = _collect_education()
    target_role = profile["experience"]["current_title"]
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
        line for line in lines
        if "=" not in line
        or line.lstrip().startswith("#")
        or line.split("=", 1)[0].strip() not in keys
    ]
    ENV_PATH.write_text("\n".join(filtered).rstrip() + "\n", encoding="utf-8")


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
        "OpenRouter (flexible multi-model), OpenAI, Anthropic, local (Ollama/llama.cpp)"
    )
    provider = Prompt.ask(
        "Provider",
        choices=list(WIZARD_PROVIDER_ORDER),
        default="gemini",
    )

    if provider == "local":
        credential = Prompt.ask(_PROVIDER_CREDENTIAL_PROMPTS[provider], default="http://localhost:8080/v1")
    else:
        credential = Prompt.ask(_PROVIDER_CREDENTIAL_PROMPTS[provider])
    model = Prompt.ask(_PROVIDER_MODEL_PROMPTS[provider], default=LLM_PROVIDER_SPECS[provider].default_model)
    spec = LLM_PROVIDER_SPECS[provider]
    other_provider_keys = [entry.env_key for entry in LLM_PROVIDER_SPECS.values() if entry.key != provider]
    _delete_env_vars(other_provider_keys)
    _upsert_env_vars({
        spec.env_key: credential,
        "LLM_MODEL": model,
    })
    console.print(f"[green]AI configuration saved to {ENV_PATH}[/green]")


# ---------------------------------------------------------------------------
# Auto-Apply
# ---------------------------------------------------------------------------

def _setup_auto_apply() -> None:
    """Configure autonomous job application (separate from the built-in LLM)."""
    from applypilot.config import get_auto_apply_agent_statuses

    console.print(Panel(
        "[bold]Step 5: Auto-Apply Agent (optional)[/bold]\n"
        "ApplyPilot can autonomously fill and submit job applications\n"
        "using a browser agent. This is separate from the Gemini/OpenRouter/OpenAI/local\n"
        "LLM you configure for scoring, tailoring, and cover letters."
    ))

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
    elif opencode_status and opencode_status.available and not statuses["codex"].available and not statuses["claude"].available:
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


# ---------------------------------------------------------------------------
# Optional documents
# ---------------------------------------------------------------------------

_OPTIONAL_FILE_KEYS = [
    ("profile_photo",  "Profile photo / headshot", [".jpg", ".jpeg", ".png", ".webp"]),
    ("id_document",    "Government-issued ID scan", [".pdf", ".jpg", ".jpeg", ".png"]),
    ("passport",       "Passport scan",             [".pdf", ".jpg", ".jpeg", ".png"]),
]


def _setup_optional_files(profile: dict, canonical_resume: dict | None = None) -> None:
    """Optionally copy documents into ~/.applypilot/files/ and record paths in profile or resume.json."""
    console.print(Panel(
        "[bold]Step 6: Optional Documents (skip if not needed)[/bold]\n"
        "Profile photo, ID, passport, certificates — some applications ask for these.\n"
        "Files are copied to [cyan]~/.applypilot/files/[/cyan] for use by the apply agent."
    ))

    if not Confirm.ask("Do you have any optional documents to add?", default=False):
        console.print("[dim]Skipped. Add files to ~/.applypilot/files/ and update ~/.applypilot/profile.json later.[/dim]")
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


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def run_wizard(resume_json: Path | None = None) -> None:
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

    canonical_result = _setup_canonical_resume(resume_json=resume_json)
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
        console.print()

    # Step 3: Search config
    _setup_searches()
    console.print()

    # Step 4: AI features (optional LLM)
    _setup_ai_features()
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
            f"[bold]Your tier: Tier {tier} — {TIER_LABELS[tier]}[/bold]\n\n"
            + "\n".join(tier_lines)
            + unlock_hint,
            border_style="green",
        )
    )
