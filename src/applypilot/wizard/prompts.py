"""Prompts."""

from __future__ import annotations

import json

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from applypilot.config import (
    PROFILE_PATH,
)
from applypilot.resume_json import (
    DEFAULT_RENDER_THEME,
    merge_resume_json_with_legacy_profile,
    normalize_profile_from_resume_json,
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


def _prompt_compensation(compensation: dict) -> dict:
    """Prompt for current salary, derive expected range, optional breakdown."""
    from applypilot.salary import clean_number, parse_range, SalaryRange

    # Skip if all key fields already present
    if all(
            str(compensation.get(k, "")).strip()
            for k in ("salary_expectation", "salary_currency", "salary_range_min", "salary_range_max")
    ):
        return compensation

    current = str(compensation.get("current_salary", "")).strip()
    if not current:
        current = Prompt.ask("Current annual salary (total comp, number)", default="")
    currency = str(compensation.get("salary_currency", "")).strip()
    if not currency:
        currency = Prompt.ask("Currency", default="USD")

    clean_current = clean_number(current)

    if clean_current > 0:
        derived = SalaryRange.from_current(clean_current)
        console.print(
            f"[dim]Derived range: {currency} {derived.range_min:,} – {derived.range_max:,} "
            f"(+40% to +100% of current)[/dim]"
        )
        if Confirm.ask("Use this derived range?", default=True):
            expected, range_min, range_max = str(derived.expected), str(derived.range_min), str(derived.range_max)
        else:
            expected = Prompt.ask("Expected annual salary (number)", default="")
            range_min, range_max = parse_range(
                Prompt.ask("Acceptable range (e.g. 80000-120000)", default=""),
                fallback=clean_number(expected),
            )
    else:
        expected = str(compensation.get("salary_expectation", "")).strip()
        if not expected:
            expected = Prompt.ask("Expected annual salary (number)", default="")
        range_min, range_max = parse_range(
            Prompt.ask("Acceptable range (e.g. 80000-120000)", default=""),
            fallback=clean_number(expected),
        )

    if Confirm.ask("Add salary breakdown (base/bonus/equity)?", default=False):
        compensation["breakdown"] = {
            "base": str(int(clean_number(Prompt.ask("Base salary", default=expected)))),
            "bonus": str(int(clean_number(Prompt.ask("Annual bonus", default="0")))),
            "equity": str(int(clean_number(Prompt.ask("Annual equity/RSU", default="0")))),
        }

    compensation.update(
        {
            "current_salary": current,
            "salary_expectation": expected,
            "salary_currency": currency or "USD",
            "salary_range_min": range_min,
            "salary_range_max": range_max,
        }
    )
    return compensation


def _prompt_target_locations(salary_usd: float, current_salary: float = 0, current_currency: str = "USD") -> dict:
    """Prompt for target locations with all/any mode, language barriers, and PPP-adjusted salary."""
    from applypilot.salary import PPPResult, SalaryRange

    console.print(
        Panel("[bold]Target Locations[/bold]\n[dim]all = must match every location, any = match at least one[/dim]")
    )
    mode = Prompt.ask("Location match mode", choices=["all", "any"], default="any")
    locations: list[dict] = []

    while True:
        loc = Prompt.ask("Target location (e.g. 'Remote', 'Germany', 'New York, NY') — empty to finish", default="")
        if not loc:
            break

        # Validate location against known countries/cities using
        # Ratcliff/Obershelp fuzzy matching (difflib.get_close_matches,
        # cutoff=0.6 catches 1-2 char typos while avoiding false positives)
        if loc.lower() not in ("remote", "anywhere", "distributed"):
            from applypilot.salary import _COUNTRY_ALIASES
            import difflib

            known = list(_COUNTRY_ALIASES.keys()) + [
                "Remote",
                "New York, NY",
                "San Francisco, CA",
                "London",
                "Berlin",
                "Tokyo",
                "Toronto",
                "Sydney",
                "Dubai",
                "Bangalore",
            ]
            # Check exact match (case-insensitive)
            matched = [k for k in known if k.lower() == loc.lower()]
            if not matched:
                # Fuzzy match for typos
                close = difflib.get_close_matches(loc, known, n=3, cutoff=0.6)
                if close:
                    suggestion = close[0]
                    fix = Prompt.ask(
                        f"  [yellow]'{loc}' not recognized. Did you mean '{suggestion}'?[/yellow]",
                        choices=["y", "n"],
                        default="y",
                    )
                    if fix == "y":
                        loc = suggestion
                    else:
                        console.print(f"  [dim]Keeping '{loc}' as-is[/dim]")
                else:
                    console.print(f"  [dim]'{loc}' not in known locations — keeping as-is[/dim]")

        entry: dict = {"name": loc}

        lang = Prompt.ask(
            f"  Language requirement for {loc} (e.g. 'English', 'German B2+') — empty if none", default=""
        )
        if lang:
            entry["language_requirement"] = lang

        # PPP-adjusted salary range for this location
        if current_salary > 0:
            ppp_range = SalaryRange.from_current_ppp(current_salary, current_currency, loc)
            if ppp_range.currency and ppp_range.currency != current_currency:
                entry["ppp_salary_range"] = {
                    "min": ppp_range.range_min,
                    "max": ppp_range.range_max,
                    "expected": ppp_range.expected,
                    "currency": ppp_range.currency,
                }
                console.print(f"  [dim]{ppp_range.note}[/dim]")
                if ppp_range.warning:
                    console.print(f"  [bold yellow]{ppp_range.warning}[/bold yellow]")
                    entry["ppp_warning"] = ppp_range.warning
                else:
                    console.print(
                        f"  [dim]→ Ask for {ppp_range.currency} {ppp_range.range_min:,} – {ppp_range.range_max:,}[/dim]"
                    )

        if salary_usd > 0:
            ppp = PPPResult.convert(salary_usd, loc)
            if ppp.known and ppp.currency != "USD":
                entry["ppp_equivalent"] = ppp.display()
                entry["ppp_rate"] = ppp.ppp_rate

        locations.append(entry)

    if not locations:
        locations.append({"name": "Remote"})

    # Relocation and country exclusions — drives the apply agent's location check
    willing_to_relocate = Prompt.ask("Willing to relocate?", choices=["yes", "no"], default="no") == "yes"
    exclude_countries: list[str] = []
    if willing_to_relocate:
        exc = Prompt.ask("Exclude countries (comma-separated, e.g. 'US, China') — empty for none", default="")
        if exc.strip():
            exclude_countries = [c.strip() for c in exc.split(",") if c.strip()]

    return {
        "mode": mode,
        "locations": locations,
        "willing_to_relocate": willing_to_relocate,
        "exclude_countries": exclude_countries,
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
    applypilot.setdefault("files", {})

    if PROFILE_PATH.exists():
        try:
            legacy_payload = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            legacy_payload = {}
        merged_resume, changed = merge_resume_json_with_legacy_profile(resume_data, legacy_payload)
        if changed:
            resume_data = merged_resume
            basics = resume_data.setdefault("basics", {})
            location = basics.setdefault("location", {})
            meta = resume_data.setdefault("meta", {})
            applypilot = meta.setdefault("applypilot", {})
            personal = applypilot.setdefault("personal", {})
            work_auth = applypilot.setdefault("work_authorization", {})
            compensation = applypilot.setdefault("compensation", {})
            availability = applypilot.setdefault("availability", {})
            eeo = applypilot.setdefault("eeo_voluntary", {})
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

    compensation = _prompt_compensation(compensation)
    applypilot["compensation"] = compensation

    # -- Target locations + language --
    if not applypilot.get("target_locations", {}).get("locations"):
        from applypilot.salary import clean_number, to_usd

        cur_salary = clean_number(compensation.get("current_salary", ""))
        cur_currency = compensation.get("salary_currency", "USD")
        salary_usd = to_usd(clean_number(compensation.get("salary_expectation", "")), cur_currency)
        applypilot["target_locations"] = _prompt_target_locations(salary_usd, cur_salary, cur_currency)

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

    if "tailoring_config" not in applypilot or not isinstance(applypilot.get("tailoring_config"), dict):
        from applypilot.wizard.profile_setup import _setup_tailoring_config  # lazy to avoid circular

        applypilot["tailoring_config"] = _setup_tailoring_config(str(applypilot.get("target_role", "")))

    applypilot.setdefault("render", {"theme": DEFAULT_RENDER_THEME})

    # Sync meta.personal URLs → basics.profiles (bug fix: URLs collected during
    # init were stored in meta but never promoted to profiles, so HTML render
    # and JSON Resume consumers couldn't see them)
    from applypilot.resume_json import _ensure_profile_url

    profiles = basics.setdefault("profiles", [])
    _ensure_profile_url(profiles, "LinkedIn", personal.get("linkedin_url", ""))
    _ensure_profile_url(profiles, "GitHub", personal.get("github_url", ""))
    if personal.get("portfolio_url"):
        _ensure_profile_url(profiles, "Portfolio", personal["portfolio_url"])
    if personal.get("website_url"):
        basics.setdefault("url", personal["website_url"])

    return resume_data
