"""Regression tests for structured education rendering.

Covers the fix for the "bare education section" bug: the tailor used to emit
education as a single ``{school} | {education_level}`` string, which the
renderer collapsed into one flat line. Education is now a structured list of
per-school entries that render as rich institution / degree / year blocks.
"""

from applypilot.scoring import pdf, validator
from applypilot.scoring.tailor import assemble_resume_text

PROFILE = {
    "personal": {"full_name": "Jordan Lee", "email": "jordan@example.com", "phone": "555-0100"},
    "experience": {"education_level": "Some College"},
    "resume_facts": {
        "preserved_school": "Riverside Community College; Lakewood College; Central High School"
    },
    "skills_boundary": {"languages": ["Python"]},
}


def _base_data(education):
    return {
        "title": "Software Engineer",
        "summary": "An engineer who builds things.",
        "skills": {"Languages": "Python"},
        "experience": [{"header": "Dev at X", "subtitle": "Py | 2020", "bullets": ["Built stuff"]}],
        "projects": [],
        "education": education,
    }


MULTI_SCHOOL = [
    {"institution": "Riverside Community College", "degree": "Coursework, Computer Science", "dates": "2010 - 2012"},
    {"institution": "Lakewood College", "degree": "General Studies", "dates": "2009 - 2010"},
    {"institution": "Central High School", "dates": "2005 - 2009"},
]


def test_structured_education_parses_into_separate_entries():
    text = assemble_resume_text(_base_data(MULTI_SCHOOL), PROFILE)
    sections = pdf.parse_resume(text)["sections"]
    entries = pdf.parse_education_entries(sections["EDUCATION"])

    # One entry per school, none collapsed into a flat legacy line.
    assert len([e for e in entries if e.get("_legacy_line")]) == 0
    institutions = [e.get("institution") for e in entries]
    assert institutions == ["Riverside Community College", "Lakewood College", "Central High School"]
    # Degree/field and years are preserved on the entries.
    assert entries[0]["startDate"] == "2010" and entries[0]["endDate"] == "2012"
    assert entries[0]["area"] == "Computer Science"


def test_structured_education_renders_rich_html_blocks():
    text = assemble_resume_text(_base_data(MULTI_SCHOOL), PROFILE)
    html = pdf.build_html(pdf.parse_resume(text))
    assert html.count('class="edu-entry"') == 3
    assert "edu-institution" in html
    assert "Riverside Community College" in html


def test_entry_without_degree_or_dates_renders_institution_only():
    text = assemble_resume_text(_base_data([{"institution": "Lakewood College"}]), PROFILE)
    entries = pdf.parse_education_entries(pdf.parse_resume(text)["sections"]["EDUCATION"])
    assert entries == [{"institution": "Lakewood College"}]


def test_multi_school_validation_passes():
    data = _base_data(MULTI_SCHOOL)
    text = assemble_resume_text(data, PROFILE)
    assert validator.validate_json_fields(data, PROFILE)["passed"]
    assert validator.validate_tailored_resume(text, PROFILE, original_text=text)["passed"]


def test_missing_school_fails_validation():
    # Drop one of the three required schools from the rendered education.
    data = _base_data(MULTI_SCHOOL[:2])
    text = assemble_resume_text(data, PROFILE)
    result = validator.validate_tailored_resume(text, PROFILE, original_text=text)
    assert not result["passed"]
    assert any("Central High School" in e for e in result["errors"])


def test_legacy_string_education_still_renders():
    # Backward compat: older tailored payloads stored education as one string.
    data = _base_data("Riverside Community College | Some College")
    text = assemble_resume_text(data, PROFILE)
    html = pdf.build_html(pdf.parse_resume(text))
    assert "Riverside Community College" in html
