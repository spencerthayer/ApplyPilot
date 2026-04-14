"""Resume Setup."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from applypilot.config import (
    PROFILE_PATH,
    RESUME_JSON_PATH,
    RESUME_PATH,
    RESUME_PDF_PATH,
)
from applypilot.resume_json import (
    DEFAULT_RENDER_THEME,
    load_resume_json_from_path,
    merge_resume_json_with_legacy_profile,
    normalize_legacy_profile,
    normalize_profile_settings,
    settings_from_resume_json,
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


from applypilot.wizard.prompts import _prompt_missing_applypilot_fields
from applypilot.wizard.profile_setup import _write_profile_json


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


def _copy_resume_json(src_path: Path) -> dict:
    data = load_resume_json_from_path(src_path)
    # Merge with existing resume.json if present — preserve profiles, contact, meta
    if RESUME_JSON_PATH.exists() and src_path.resolve() != RESUME_JSON_PATH.resolve():
        try:
            existing = load_resume_json_from_path(RESUME_JSON_PATH)
            data = _merge_incoming_resume(data, existing)
            console.print(f"[green]Merged incoming resume with existing {RESUME_JSON_PATH}[/green]")
        except Exception:
            console.print("[yellow]Could not merge with existing resume, using incoming file as-is.[/yellow]")
    _write_resume_json(data)
    return data


def _merge_incoming_resume(incoming: dict, existing: dict) -> dict:
    """Merge incoming resume with existing — incoming wins for content, existing fills gaps."""
    import copy

    merged = copy.deepcopy(incoming)

    # Preserve existing basics fields that incoming doesn't have
    ex_basics = existing.get("basics", {})
    m_basics = merged.setdefault("basics", {})
    for key in ("email", "phone", "url", "image"):
        if not m_basics.get(key) and ex_basics.get(key):
            m_basics[key] = ex_basics[key]

    # Merge profiles — add existing profiles not in incoming
    incoming_profiles = m_basics.get("profiles", [])
    existing_profiles = ex_basics.get("profiles", [])
    incoming_networks = {p.get("network", "").lower() for p in incoming_profiles}
    for ep in existing_profiles:
        if ep.get("network", "").lower() not in incoming_networks:
            incoming_profiles.append(ep)
    m_basics["profiles"] = incoming_profiles

    # Preserve existing meta.applypilot settings that incoming doesn't have
    ex_meta = existing.get("meta", {}).get("applypilot", {})
    m_meta = merged.setdefault("meta", {}).setdefault("applypilot", {})
    for key in ex_meta:
        if key not in m_meta:
            m_meta[key] = ex_meta[key]

    return merged


def _decompose_resume(resume_data: dict) -> None:
    """Decompose canonical resume.json into atomic pieces via PieceRepository."""
    from applypilot.bootstrap import get_app
    from applypilot.tailoring.pieces.decomposer import decompose_to_pieces

    pieces = decompose_to_pieces(resume_data, get_app().container.piece_repo)

    bullet_count = sum(1 for p in pieces if p.piece_type == "bullet")
    console.print(f"[green]Decomposed resume into {len(pieces)} pieces ({bullet_count} bullets)[/green]")


def _generate_variants_background(resume_data: dict) -> None:
    """Placeholder — variant generation now uses piece-based track architecture."""
    pass


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

    console.print(
        Panel(
            "[bold]Step 1: Resume Source[/bold]\n"
            "Choose how to create your canonical [cyan]resume.json[/cyan].\n"
            "[dim]pdf — import from PDF/TXT files (requires LLM)[/dim]"
        )
    )
    choice = Prompt.ask(
        "Resume setup mode",
        choices=["pdf", "import", "migrate", "scaffold", "legacy"],
        default="pdf",
    )

    if choice == "legacy":
        return None
    if choice == "pdf":
        from applypilot.wizard.env_setup import _ensure_llm_configured  # lazy to avoid circular

        _ensure_llm_configured()
        path_str = Prompt.ask("Resume file path(s), comma-separated")
        paths = [Path(p.strip().strip('"').strip("'")).expanduser().resolve() for p in path_str.split(",") if p.strip()]
        if not paths:
            console.print("[red]No file paths provided.[/red]")
            return None
        for p in paths:
            if not p.exists():
                console.print(f"[red]File not found:[/red] {p}")
                return None
            if p.suffix.lower() not in (".pdf", ".txt", ".md"):
                console.print(f"[red]Unsupported file type:[/red] {p.suffix or '(none)'}. Use .pdf or .txt")
                return None
        from applypilot.resume_ingest import ingest_resumes

        console.print("[dim]Parsing resume(s) via LLM...[/dim]")
        data = ingest_resumes(paths)
        _write_resume_json(data)
        console.print(f"[green]Imported resume into {RESUME_JSON_PATH}[/green]")
    elif choice == "import":
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


def _profile_for_canonical_resume(resume_data: dict) -> dict:
    """Return the authoritative profile for a canonical resume setup."""

    if PROFILE_PATH.exists():
        try:
            payload = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            merged_resume, changed = merge_resume_json_with_legacy_profile(resume_data, payload)
            if changed:
                resume_data.clear()
                resume_data.update(merged_resume)
                _write_resume_json(resume_data)
            profile = normalize_profile_settings(payload)
            _write_profile_json(profile)
            return profile
        except json.JSONDecodeError:
            pass

    profile = settings_from_resume_json(resume_data)
    _write_profile_json(profile)
    return profile


def _legacy_profile_to_resume_json(profile: dict, resume_text: str = "") -> dict:
    from applypilot.scoring.pdf import parse_entries, parse_resume, parse_skills

    parsed_resume = (
        parse_resume(resume_text) if resume_text.strip() else {"sections": {}, "name": "", "title": "", "contact": ""}
    )
    sections = parsed_resume.get("sections", {})

    normalized = normalize_legacy_profile(profile)
    personal = normalized.get("personal", {})
    experience = normalized.get("experience", {})
    work_entries = normalized.get("work", [])
    education = normalized.get("education", [])
    skills = normalized.get("skills", [])
    projects_from_profile = normalized.get("projects", [])

    basics = {
        "name": personal.get("full_name", "") or parsed_resume.get("name", ""),
        "label": experience.get("target_role", "")
                 or experience.get("current_title", "")
                 or parsed_resume.get("title", ""),
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
    if work_entries:
        for index, role in enumerate(work_entries):
            parsed_entry = parsed_experience[index] if index < len(parsed_experience) else {}
            highlights = parsed_entry.get("bullets", []) if parsed_entry else role.get("highlights", [])
            work.append(
                {
                    "name": role.get("company", ""),
                    "position": role.get("position", ""),
                    "location": role.get("location", ""),
                    "startDate": str(role.get("start_date") or ""),
                    "endDate": str(role.get("end_date") or ""),
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
    if projects_from_profile:
        for project in projects_from_profile:
            projects.append(
                {
                    "name": project.get("name", ""),
                    "description": project.get("description", ""),
                    "highlights": project.get("highlights", []),
                    "url": project.get("url", ""),
                }
            )
    else:
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
    elif skills:
        canonical_skills = skills

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
    console.print(
        Panel(
            "[bold]Step 1: Canonical Resume[/bold]\nCreate a starter [cyan]resume.json[/cyan] that follows JSON Resume."
        )
    )
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
