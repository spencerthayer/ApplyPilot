import pytest

from applypilot.scoring.validator import validate_json_fields


def make_profile(target_title: str = ""):
    return {
        "skills": [{"name": "Languages", "keywords": ["Python"]}],
        "work": [{"company": "Acme Corp", "position": "Engineer", "start_date": "2020-01-01", "end_date": ""}],
        "education": [{"institution": "Acme University", "studyType": "B.S.", "area": "Computer Science", "endDate": "2020"}],
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
