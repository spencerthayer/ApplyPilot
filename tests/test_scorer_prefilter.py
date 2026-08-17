"""Tests for the pre-filter patterns in scorer.py.

Validated 2026-04-23 against 5,938 historical scored jobs. Any pattern
that rejects a job with LLM score >= 8 was either a clear LLM mis-score
(Junior/Intern roles scored high) or a dual-geography edge case.
"""

import pytest

from applypilot.scoring.scorer import _check_ineligible


def _job(title="Senior Software Engineer", location="Remote", description="US-remote position."):
    return {"title": title, "location": location, "full_description": description}


# ── Seniority rejects ───────────────────────────────────────────────

def test_junior_title_rejected():
    assert _check_ineligible(_job(title="Junior Software Engineer")) is not None


def test_intern_title_rejected():
    assert _check_ineligible(_job(title="Platform Engineering Intern")) is not None


def test_internship_title_rejected():
    assert _check_ineligible(_job(title="Software Engineering Internship")) is not None


def test_fresher_title_rejected():
    assert _check_ineligible(_job(title="Fresher Software Engineer")) is not None


def test_entry_level_title_rejected():
    assert _check_ineligible(_job(title="Software Engineer I - Entry Level")) is not None


def test_new_grad_title_rejected():
    assert _check_ineligible(_job(title="New Grad Software Engineer")) is not None


def test_trainee_title_rejected():
    assert _check_ineligible(_job(title="Software Engineer Trainee")) is not None


def test_apprentice_title_rejected():
    assert _check_ineligible(_job(title="Software Apprentice")) is not None


# ── Sales-adjacency rejects ─────────────────────────────────────────

def test_sales_engineer_rejected():
    assert _check_ineligible(_job(title="Senior Sales Engineer")) is not None


def test_solutions_engineer_rejected():
    assert _check_ineligible(_job(title="Solutions Engineer")) is not None


def test_presales_rejected():
    assert _check_ineligible(_job(title="Senior Presales Engineer")) is not None


def test_customer_success_engineer_rejected():
    assert _check_ineligible(_job(title="Senior Customer Success Engineer")) is not None


# ── Additional safe patterns (validated 2026-04-23 round 2) ─────────

def test_graduate_title_rejected():
    assert _check_ineligible(_job(title="Graduate Developer")) is not None
    assert _check_ineligible(_job(title="Graduate Software Engineer")) is not None


def test_recruiter_title_rejected():
    assert _check_ineligible(_job(title="Technical Recruiter")) is not None
    assert _check_ineligible(_job(title="Senior Talent Acquisition Partner")) is not None


def test_account_manager_title_rejected():
    assert _check_ineligible(_job(title="Senior Account Manager")) is not None
    assert _check_ineligible(_job(title="Enterprise Account Executive")) is not None


def test_designer_title_rejected():
    assert _check_ineligible(_job(title="Senior UX Designer")) is not None
    assert _check_ineligible(_job(title="Product Designer")) is not None
    assert _check_ineligible(_job(title="Graphic Designer")) is not None


def test_mobile_only_title_rejected():
    assert _check_ineligible(_job(title="Senior Android Engineer")) is not None
    assert _check_ineligible(_job(title="iOS Engineer")) is not None
    assert _check_ineligible(_job(title="Mobile Engineer")) is not None


def test_legacy_stack_title_rejected():
    assert _check_ineligible(_job(title="Salesforce Developer")) is not None
    assert _check_ineligible(_job(title="Apex Developer")) is not None
    assert _check_ineligible(_job(title="Mainframe Engineer (COBOL)")) is not None
    assert _check_ineligible(_job(title="Senior TIBCO Developer")) is not None


def test_senior_backend_python_not_rejected():
    """Ensure backend/platform engineer titles stay clean."""
    assert _check_ineligible(_job(title="Senior Backend Engineer, Python")) is None
    assert _check_ineligible(_job(title="Staff Platform Engineer - Go")) is None
    assert _check_ineligible(_job(title="Principal Software Engineer")) is None


# ── Regional sales tags ─────────────────────────────────────────────

def test_latam_in_title_rejected():
    assert _check_ineligible(_job(title="Account Manager, LATAM")) is not None


def test_mena_in_title_rejected():
    assert _check_ineligible(_job(title="Engineering Lead, MENA")) is not None


def test_anz_in_title_rejected():
    assert _check_ineligible(_job(title="Senior DevOps Engineer, ANZ")) is not None


def test_nordics_in_title_rejected():
    assert _check_ineligible(_job(title="Staff Engineer - Nordics")) is not None


def test_only_hiring_in_title_rejected():
    assert _check_ineligible(_job(title="Only hiring in Vietnam | Senior Engineer")) is not None


# ── New non-US countries in location ────────────────────────────────

@pytest.mark.parametrize("loc", [
    "Brazil Remote Work",
    "Remote — São Paulo, Brazil",
    "Mexico City, Mexico",
    "Buenos Aires, Argentina",
    "Vietnam",
    "Remote - Japan",
    "Thailand",
    "Philippines",
    "Jakarta, Indonesia",
    "Seoul, Korea",
    "Taipei, Taiwan",
    "Cairo, Egypt",
    "Nairobi, Kenya",
    "Johannesburg, South Africa",
    "Tel Aviv, Israel",
    "Istanbul, Turkey",
    "Lisbon, Portugal",
    "Dublin, Ireland",
    "Copenhagen, Denmark",
    "Stockholm, Sweden",
    "Oslo, Norway",
    "Helsinki, Finland",
    "Brussels, Belgium",
    "Zurich, Switzerland",
    "Vienna, Austria",
    "Bucharest, Romania",
    "Budapest, Hungary",
])
def test_non_us_country_in_location_rejected(loc):
    assert _check_ineligible(_job(location=loc)) is not None


# ── Must NOT reject legit US roles ──────────────────────────────────

def test_senior_us_remote_not_rejected():
    assert _check_ineligible(_job(title="Senior Software Engineer", location="Remote (US)")) is None


def test_staff_engineer_not_rejected():
    assert _check_ineligible(_job(title="Staff Software Engineer", location="Seattle, WA")) is None


def test_principal_engineer_not_rejected():
    assert _check_ineligible(_job(title="Principal Platform Engineer", location="San Francisco, CA")) is None


def test_senior_with_global_office_mention_not_rejected():
    """A US role mentioning a global office in the description should NOT be rejected.

    The description pre-filter is narrow — requires explicit regional restrictions
    like 'Remote (Europe)' or 'EMEA only', not a casual office mention.
    """
    desc = "We're a US-based company with offices in San Francisco, London, and Tokyo. "
    desc += "This role is US-remote. You'll work on distributed systems."
    assert _check_ineligible(_job(description=desc)) is None


def test_associate_director_not_rejected():
    """Ensure 'Associate' doesn't falsely match 'Entry Level'-like patterns."""
    assert _check_ineligible(_job(title="Associate Director of Engineering")) is None


# ── 2026-04-30: Buried-in-description non-US restrictions ───────────
#
# Twilio's Greenhouse postings (jobs/7662058 and 7767260) titled their
# roles "Senior Software Engineer" with location "Remote", but the
# country restriction was buried in the description body like:
#   "This role will be remote and based in the UK."
#   "This role will be remote and based in Ontario, BC or Alberta, Canada."
# The prior 800-char head-only scan missed those. Bumped to 6000 + new
# patterns. Each test below corresponds to a real failed-apply URL.

def test_twilio_uk_remote_buried_in_description_rejected():
    """Twilio jobs/7662058 — Senior SWE with UK location buried in desc."""
    desc = (
        "About Twilio: We are looking for an exceptional Senior Software "
        "Engineer to join our team. " * 30  # pushes the restriction past 800 chars
        + " The mission of this team is to build the foundational platform. "
        + "This role will be remote and based in the UK."
    )
    result = _check_ineligible(_job(
        title="Senior Software Engineer",
        location="Remote",
        description=desc,
    ))
    assert result is not None
    assert "non-US geography in description" in result


def test_twilio_canada_provinces_rejected():
    """Twilio jobs/7767260 — L3 SWE with Canada-province restriction."""
    desc = (
        "About Twilio: " + "blah " * 200
        + " This role will be remote and based in Ontario, British Columbia or Alberta, Canada."
    )
    result = _check_ineligible(_job(
        title="Software Engineer (L3) Infrastructure",
        location="Remote",
        description=desc,
    ))
    assert result is not None


def test_based_in_uk_rejected():
    """Variant: 'based in the UK' anywhere in first 6000 chars."""
    desc = "We are a global team. " * 50 + "Candidates must be based in the UK."
    assert _check_ineligible(_job(description=desc)) is not None


def test_right_to_work_uk_rejected():
    """JD mentions 'right to work in the UK' question."""
    desc = "Senior role. " * 100 + "You must have the right to work in the United Kingdom."
    assert _check_ineligible(_job(description=desc)) is not None


def test_ist_timezone_rejected():
    """India Standard Time requirement."""
    desc = "Engineering role. " * 50 + "Candidates must work IST timezone hours."
    assert _check_ineligible(_job(description=desc)) is not None


# ── _parse_score_response: ELIGIBILITY field extraction ─────────────

def test_parser_extracts_eligibility_eligible():
    from applypilot.scoring.scorer import _parse_score_response
    response = """ELIGIBILITY: eligible
SCORE: 9
KEYWORDS: Python, Go, Kubernetes
REASONING: Strong stack match, US remote role."""
    parsed = _parse_score_response(response)
    assert parsed["score"] == 9
    assert parsed["eligibility"] == "eligible"


def test_parser_extracts_eligibility_non_us_only():
    from applypilot.scoring.scorer import _parse_score_response
    response = """ELIGIBILITY: non_us_only
SCORE: 8
KEYWORDS: backend
REASONING: UK-only remote role."""
    parsed = _parse_score_response(response)
    assert parsed["eligibility"] == "non_us_only"


def test_parser_eligibility_with_brackets():
    """The prompt template shows [eligible|non_us_only] — make sure
    ``ELIGIBILITY: [non_us_only]`` (the LLM keeping the brackets) parses."""
    from applypilot.scoring.scorer import _parse_score_response
    response = "ELIGIBILITY: [non_us_only]\nSCORE: 7\nKEYWORDS: foo\nREASONING: bar"
    parsed = _parse_score_response(response)
    assert parsed["eligibility"] == "non_us_only"


def test_parser_missing_eligibility_defaults_to_eligible():
    """Older models that omit the field — default to eligible (no false bans)."""
    from applypilot.scoring.scorer import _parse_score_response
    response = "SCORE: 8\nKEYWORDS: foo\nREASONING: bar"
    parsed = _parse_score_response(response)
    assert parsed["eligibility"] == "eligible"
