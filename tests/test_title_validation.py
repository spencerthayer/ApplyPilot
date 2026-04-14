import pytest

from applypilot.scoring.validator import validate_json_fields


def make_profile(target_title: str = "", companies: list[str] | None = None):
    companies = companies or ["Acme Corp"]
    work = [
        {"company": company, "position": "Engineer", "start_date": "2020-01-01", "end_date": ""}
        for company in companies
    ]
    return {
        "skills": [{"name": "Languages", "keywords": ["Python"]}],
        "work": work,
        "education": [
            {"institution": "Acme University", "studyType": "B.S.", "area": "Computer Science", "endDate": "2020"}
        ],
        "job_context": {"title": target_title},
    }


def test_title_close_match_passes():
    profile = make_profile("Senior Software Engineer")
    data = {
        "title": "Senior Software Engineer",
        "summary": "Experienced engineer.",
        "skills": {"Languages": "Python"},
        "experience": [{"header": "Senior Software Engineer at Acme Corp", "bullets": ["Did work"]}],
        "projects": [{"header": "Project Alpha", "subtitle": "Python | 2023", "bullets": ["Built X"]}],
        "education": "Acme University | B.S. Computer Science",
    }

    res = validate_json_fields(data, profile, mode="normal")
    assert res["passed"], f"Expected pass but got errors: {res['errors']}"


def test_title_odd_specialization_fails():
    profile = make_profile("Senior Software Engineer")
    data = {
        "title": "Alliances Partner Engineer",
        "summary": "Experienced engineer.",
        "skills": {"Languages": "Python"},
        "experience": [{"header": "Senior Software Engineer at Acme Corp", "bullets": ["Did work"]}],
        "projects": [{"header": "Project Beta", "subtitle": "Python | 2022", "bullets": ["Implemented Y"]}],
        "education": "Acme University | B.S. Computer Science",
    }

    res = validate_json_fields(data, profile, mode="normal")
    assert not res["passed"], "Expected failure for odd/specialized generated title"
    assert any("Generated title" in e for e in res["errors"]), "Expected generated title error"


def test_normal_mode_allows_missing_historical_companies_with_warning():
    profile = make_profile("Senior Software Engineer", companies=["Acme Corp", "Old Company"])
    data = {
        "title": "Senior Software Engineer",
        "summary": "Experienced engineer.",
        "skills": {"Languages": "Python"},
        "experience": [{"header": "Senior Software Engineer at Acme Corp", "bullets": ["Did work"]}],
        "projects": [{"header": "Project Alpha", "subtitle": "Python | 2023", "bullets": ["Built X"]}],
        "education": "Acme University | B.S. Computer Science",
    }

    res = validate_json_fields(data, profile, mode="normal")
    assert res["passed"], f"Expected pass in normal mode but got errors: {res['errors']}"
    assert any("Old Company" in warning for warning in res["warnings"]), (
        "Expected warning for missing historical company"
    )
    assert not any("missing from experience" in error for error in res["errors"])


def test_normal_mode_requires_at_least_one_profile_company():
    profile = make_profile("Senior Software Engineer", companies=["Acme Corp", "Old Company"])
    data = {
        "title": "Senior Software Engineer",
        "summary": "Experienced engineer.",
        "skills": {"Languages": "Python"},
        "experience": [{"header": "Senior Software Engineer at Other Corp", "bullets": ["Did work"]}],
        "projects": [{"header": "Project Alpha", "subtitle": "Python | 2023", "bullets": ["Built X"]}],
        "education": "Acme University | B.S. Computer Science",
    }

    res = validate_json_fields(data, profile, mode="normal")
    assert not res["passed"], "Expected failure when no profile company is present in normal mode"
    assert any("No profile companies found in experience" in error for error in res["errors"])


def test_normal_mode_accepts_company_in_subtitle():
    profile = make_profile("Senior Software Engineer", companies=["Watson Creative", "Old Company"])
    data = {
        "title": "Senior Software Engineer",
        "summary": "Experienced engineer.",
        "skills": {"Languages": "Python"},
        "experience": [
            {
                "header": "Principal Developer",
                "subtitle": "Watson Creative | 2022-09 - 2026-02",
                "bullets": ["Did work"],
            }
        ],
        "projects": [{"header": "Project Alpha", "subtitle": "Python | 2023", "bullets": ["Built X"]}],
        "education": "Acme University | B.S. Computer Science",
    }

    res = validate_json_fields(data, profile, mode="normal")
    assert res["passed"], f"Expected subtitle company match in normal mode, got errors: {res['errors']}"
    assert not any("No profile companies found in experience" in error for error in res["errors"])


def test_strict_mode_still_requires_all_companies():
    profile = make_profile("Senior Software Engineer", companies=["Acme Corp", "Old Company"])
    data = {
        "title": "Senior Software Engineer",
        "summary": "Experienced engineer.",
        "skills": {"Languages": "Python"},
        "experience": [{"header": "Senior Software Engineer at Acme Corp", "bullets": ["Did work"]}],
        "projects": [{"header": "Project Alpha", "subtitle": "Python | 2023", "bullets": ["Built X"]}],
        "education": "Acme University | B.S. Computer Science",
    }

    res = validate_json_fields(data, profile, mode="strict")
    assert not res["passed"], "Expected strict mode to fail when any company is missing"
    assert any("Old Company" in error for error in res["errors"])


def test_lenient_mode_skips_company_retention_checks():
    profile = make_profile("Senior Software Engineer", companies=["Acme Corp", "Old Company"])
    data = {
        "title": "Senior Software Engineer",
        "summary": "Experienced engineer.",
        "skills": {"Languages": "Python"},
        "experience": [{"header": "Senior Software Engineer at Other Corp", "bullets": ["Did work"]}],
        "projects": [{"header": "Project Alpha", "subtitle": "Python | 2023", "bullets": ["Built X"]}],
        "education": "Acme University | B.S. Computer Science",
    }

    res = validate_json_fields(data, profile, mode="lenient")
    assert res["passed"], f"Expected lenient mode to skip company retention checks, got: {res['errors']}"
    assert not any("Company '" in error for error in res["errors"])
    assert not any("Company '" in warning for warning in res["warnings"])


@pytest.mark.parametrize("mode", ["normal", "strict"])
def test_fabricated_skill_is_hard_error(mode: str):
    profile = make_profile("Senior Software Engineer")
    data = {
        "title": "Senior Software Engineer",
        "summary": "Experienced engineer.",
        "skills": {"Languages": "Python, Rust"},
        "experience": [{"header": "Senior Software Engineer at Acme Corp", "bullets": ["Did work"]}],
        "projects": [{"header": "Project Alpha", "subtitle": "Python | 2023", "bullets": ["Built X"]}],
        "education": "Acme University | B.S. Computer Science",
    }

    res = validate_json_fields(data, profile, mode=mode)
    assert not res["passed"], f"Expected fabricated skill to fail in {mode} mode"
    assert any("Fabricated skill: 'rust'" in error for error in res["errors"])
