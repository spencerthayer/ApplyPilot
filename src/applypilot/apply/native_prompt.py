"""Lean prompt builder for the native Playwright agent.

Design: Data only, no strategy. All strategy lives in native_agent._SYSTEM_PROMPT.
The user prompt is a structured data payload the LLM reads from, not a set of
instructions it follows. This prevents the user prompt from contradicting the
system prompt — the root cause of all three production failures (Affirm, Cloudflare x2).

SRP: Only builds the prompt string. Does not run the agent, manage Chrome, or
parse results.
"""

from __future__ import annotations

import json

from applypilot import config

# ATS-specific hints injected as a single line. The system prompt defines
# the generic workflow; these hints tell the LLM which dropdown pattern
# to expect on this specific site.
_ATS_HINTS: dict[str, str] = {
    "greenhouse.io": (
        "ATS: Greenhouse. Form appears after clicking Apply. "
        "Country/Location/screening dropdowns are combobox — use click→type→snapshot→click pattern."
    ),
    "myworkdayjobs.com": (
        "ATS: Workday. Multi-page wizard. Fill each page, click Next. Repeat snapshot→fill→next until Submit."
    ),
    "lever.co": "ATS: Lever. Single-page form. Dropdowns are real <select> — use browser_select_option.",
    "applytojob.com": "ATS: Lever. Single-page form. Dropdowns are real <select> — use browser_select_option.",
    "linkedin.com": (
        "ATS: LinkedIn Easy Apply. Click 'Easy Apply' button. "
        "Fill modal fields, click Next each step. Final step: Submit."
    ),
    "ashbyhq.com": "ATS: Ashby. Simple form — name, email, resume upload.",
    "icims.com": "ATS: iCIMS. May use iframes. Check browser_tabs if form opens in new tab.",
    "taleo.net": "ATS: Taleo. Multi-page wizard similar to Workday.",
}


def _get_ats_hint(url: str) -> str:
    """Detect ATS type from URL domain and return a one-line hint."""
    from urllib.parse import urlparse

    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return "ATS: Unknown. Use standard workflow: snapshot → fill → submit."
    for domain, hint in _ATS_HINTS.items():
        if domain in host:
            return hint
    return "ATS: Unknown. Use standard workflow: snapshot → fill → submit."


def build_native_prompt(
    job: dict,
    resume_text: str,
    resume_pdf_path: str,
    cover_letter_pdf_path: str | None = None,
    dry_run: bool = False,
) -> str:
    """Build a data-only prompt for the native agent.

    Returns ~800 words of structured data. No strategy, no tool usage
    instructions, no browser_evaluate examples. The system prompt handles all
    of that.
    """
    profile = config.load_profile()
    personal = profile.get("personal", {})
    work_auth = profile.get("work_authorization", {})
    comp = profile.get("compensation", {})
    exp = profile.get("experience", {})

    job_url = job.get("application_url") or job["url"]
    # Resolve relative application URLs against the job page domain
    if job_url.startswith("/"):
        from urllib.parse import urlparse

        parsed = urlparse(job["url"])
        job_url = f"{parsed.scheme}://{parsed.netloc}{job_url}"
    ats_hint = _get_ats_hint(job_url)

    # Structured profile — LLM maps these directly to form fields.
    # Keys match common form labels so the LLM can do a direct lookup.
    profile_data = {
        "first_name": personal.get("full_name", "").split()[0] if personal.get("full_name") else "",
        "last_name": " ".join(personal.get("full_name", "").split()[1:]),
        "full_name": personal.get("full_name", ""),
        "email": personal.get("email", ""),
        "phone": personal.get("phone", ""),
        "address": personal.get("address", ""),
        "city": personal.get("city", ""),
        "state": personal.get("province_state", ""),
        "country": personal.get("country", ""),
        "postal_code": personal.get("postal_code", ""),
        "linkedin": personal.get("linkedin_url", ""),
        "github": personal.get("github_url", ""),
        "portfolio": personal.get("portfolio_url", ""),
        "work_authorized": work_auth.get("legally_authorized_to_work", ""),
        "sponsorship_needed": work_auth.get("require_sponsorship", ""),
        "salary": f"{comp.get('salary_expectation', '')} {comp.get('salary_currency', 'USD')}".strip(),
        "years_experience": exp.get("years_of_experience_total", ""),
        "education": exp.get("education_level", ""),
        "current_title": exp.get("current_job_title", ""),
        "available_start": profile.get("availability", {}).get("earliest_start_date", "Immediately"),
    }

    screening = {
        "age_18_plus": "Yes",
        "background_check": "Yes",
        "felony": "No",
        "previously_worked_here": "No",
        "how_heard": "Online Job Board",
        "gender": "Decline to self-identify",
        "race_ethnicity": "Decline to self-identify",
        "veteran_status": "I am not a protected veteran",
        "disability": "I do not wish to answer",
    }

    # Merge EEO from profile if available
    eeo = profile.get("eeo_voluntary", {})
    if eeo.get("gender"):
        screening["gender"] = eeo["gender"]
    if eeo.get("race_ethnicity"):
        screening["race_ethnicity"] = eeo["race_ethnicity"]
    if eeo.get("veteran_status"):
        screening["veteran_status"] = eeo["veteran_status"]
    if eeo.get("disability_status"):
        screening["disability"] = eeo["disability_status"]

    submit_note = ""
    if dry_run:
        submit_note = "\n== DRY RUN: Do NOT click Submit. Verify all fields, then output RESULT:APPLIED ==\n"

    return f"""\
== JOB ==
URL: {job_url}
Title: {job.get("title", "Unknown")}
Company: {job.get("site", "Unknown")}
Score: {job.get("fit_score", "?")}

== {ats_hint} ==

== FILES ==
Resume PDF: {resume_pdf_path}
Cover Letter PDF: {cover_letter_pdf_path or "N/A — skip if optional, write 2 sentences if required"}

== APPLICANT PROFILE (use these values for form fields) ==
{json.dumps(profile_data, indent=2)}

== SCREENING DEFAULTS ==
{json.dumps(screening, indent=2)}

== SALARY ==
Floor: {comp.get("salary_expectation", "")} {comp.get("salary_currency", "USD")}.
If job shows a range → midpoint. If asked for range → floor to floor+20%. If hourly → annual/2080.

== OPEN-ENDED QUESTIONS ==
Write 2-3 sentences. Reference {job.get("title", "the role")} at {job.get("site", "the company")}. Connect to resume skills. Never "I am passionate about..."

== HARD RULES ==
Never lie about: work authorization, citizenship, criminal history, education.
{submit_note}
== RESUME SUMMARY ==
{resume_text[:600]}
"""
