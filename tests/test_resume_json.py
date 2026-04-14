from __future__ import annotations

import json

import pytest

from applypilot.resume_json import (
    ResumeJsonError,
    build_resume_text_from_json,
    get_profile_verified_metrics,
    load_resume_json_from_path,
    normalize_profile_from_resume_json,
    resolve_render_theme,
    validate_resume_json,
)


def sample_resume_json() -> dict:
    return {
        "basics": {
            "name": "Spencer Thayer",
            "label": "Systems Architect",
            "email": "me@example.com",
            "phone": "555-123-4567",
            "url": "https://example.com",
            "summary": "Built production systems across design and engineering roles.",
            "location": {
                "city": "Portland",
                "region": "OR",
                "countryCode": "US",
                "postalCode": "97203",
            },
            "profiles": [
                {"network": "LinkedIn", "url": "https://linkedin.com/in/example"},
                {"network": "GitHub", "url": "https://github.com/example"},
            ],
        },
        "work": [
            {
                "name": "Watson Creative",
                "position": "Principal Developer",
                "location": "Portland, OR",
                "startDate": "2022-09-19",
                "summary": "Led platform strategy.",
                "highlights": ["Built client platforms", "Automated AI workflows"],
                "x-applypilot": {"key_metrics": ["99.9% uptime", "50% faster delivery"]},
            }
        ],
        "education": [
            {
                "institution": "Lincoln Land Community College",
                "studyType": "Associate",
                "area": "Liberal Arts",
                "endDate": "2000",
            }
        ],
        "skills": [
            {"name": "Programming Languages", "keywords": ["Python", "JavaScript"]},
            {"name": "Frameworks & Libraries", "keywords": ["FastAPI", "React"]},
            {"name": "Tools & Platforms", "keywords": ["Docker", "AWS"]},
        ],
        "projects": [
            {
                "name": "capstack.ai",
                "description": "Digital loan marketplace platform.",
                "highlights": ["Shipped platform MVP"],
                "url": "https://capstack.ai",
            }
        ],
        "meta": {
            "theme": "jsonresume-theme-even",
            "applypilot": {
                "target_role": "Staff Software Engineer",
                "years_of_experience_total": "20",
                "work_authorization": {
                    "legally_authorized_to_work": "Yes",
                    "require_sponsorship": "No",
                },
                "compensation": {
                    "salary_expectation": "180000",
                    "salary_currency": "USD",
                    "salary_range_min": "170000",
                    "salary_range_max": "210000",
                },
                "availability": {"earliest_start_date": "Immediately"},
                "render": {"theme": "jsonresume-theme-even"},
            },
        },
    }


def test_normalize_profile_from_resume_json_maps_internal_contract() -> None:
    profile = normalize_profile_from_resume_json(sample_resume_json())

    assert profile["personal"]["full_name"] == "Spencer Thayer"
    assert profile["personal"]["linkedin_url"] == "https://linkedin.com/in/example"
    assert profile["experience"]["current_title"] == "Principal Developer"
    assert profile["experience"]["target_role"] == "Staff Software Engineer"
    assert profile["work"][0]["company"] == "Watson Creative"
    assert get_profile_verified_metrics(profile) == [
        "99.9% uptime",
        "50% faster delivery",
    ]


def test_build_resume_text_from_json_is_deterministic() -> None:
    data = sample_resume_json()

    first = build_resume_text_from_json(data)
    second = build_resume_text_from_json(data)

    assert first == second
    assert "SUMMARY" in first
    assert "TECHNICAL SKILLS" in first
    assert "EXPERIENCE" in first
    assert "PROJECTS" in first
    assert "EDUCATION" in first
    assert "Spencer Thayer" in first


def test_build_resume_reverse_chronological() -> None:
    """Work entries must be sorted present → past (reverse chronological)."""
    data = sample_resume_json()
    data["work"] = [
        {
            "name": "OldCo",
            "position": "Junior",
            "startDate": "2018-01-01",
            "endDate": "2020-01-01",
            "highlights": ["Old work"],
        },
        {"name": "NewCo", "position": "Senior", "startDate": "2023-01-01", "highlights": ["New work"]},
    ]
    text = build_resume_text_from_json(data)
    assert text.index("NewCo") < text.index("OldCo")


def test_build_resume_omits_empty_projects() -> None:
    """PROJECTS header must not appear when there are no project entries."""
    data = sample_resume_json()
    data["projects"] = []
    text = build_resume_text_from_json(data)
    assert "PROJECTS" not in text


def test_build_resume_omits_empty_skills() -> None:
    """TECHNICAL SKILLS header must not appear when skills are empty."""
    data = sample_resume_json()
    data["skills"] = []
    text = build_resume_text_from_json(data)
    assert "TECHNICAL SKILLS" not in text


def test_build_resume_no_na() -> None:
    """N/A must never appear in rendered resume."""
    data = sample_resume_json()
    data["projects"] = []
    text = build_resume_text_from_json(data)
    assert "N/A" not in text


def test_resolve_render_theme_prefers_applypilot_then_meta() -> None:
    data = sample_resume_json()
    assert resolve_render_theme(data) == "jsonresume-theme-even"
    assert resolve_render_theme(data, explicit_theme="jsonresume-theme-professional") == "jsonresume-theme-professional"


def test_normalize_profile_uses_first_role_from_multi_role_label_when_target_role_missing() -> None:
    data = sample_resume_json()
    data["meta"]["applypilot"].pop("target_role", None)
    data["basics"]["label"] = "Systems Architect, Senior Full Stack Developer, UI/UX"

    profile = normalize_profile_from_resume_json(data)

    assert profile["experience"]["target_role"] == "Systems Architect"


def test_validate_resume_json_rejects_secret_keys() -> None:
    pytest.importorskip("jsonschema")
    data = sample_resume_json()
    data["meta"]["applypilot"]["personal"] = {"password": "secret"}

    with pytest.raises(ResumeJsonError, match="secrets must stay in .env"):
        validate_resume_json(data)


def test_load_resume_json_from_path_fails_on_invalid_canonical(tmp_path) -> None:
    pytest.importorskip("jsonschema")
    path = tmp_path / "resume.json"
    path.write_text(json.dumps({"basics": [], "meta": {"applypilot": {}}}), encoding="utf-8")

    with pytest.raises(ResumeJsonError, match="Invalid resume.json"):
        load_resume_json_from_path(path)
