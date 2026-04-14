"""Tests for resume assembler — empty section handling."""

import pytest
from applypilot.scoring.tailor.response_assembler import assemble_resume_text


class TestAssemblerEmptySections:
    """Empty sections must not render — no empty headers, no N/A."""

    @pytest.fixture
    def profile(self):
        return {
            "personal": {"full_name": "Test User", "email": "t@t.com"},
            "basics": {"name": "Test User", "email": "t@t.com", "profiles": []},
        }

    def test_empty_projects_not_rendered(self, profile):
        data = {
            "title": "SDE",
            "summary": "Good engineer.",
            "skills": {"Languages": "Python"},
            "experience": [{"header": "SDE | Co | 2024 - Present", "bullets": [{"text": "Built stuff"}]}],
            "projects": [],
            "education": "MIT | BS | 2022",
        }
        text = assemble_resume_text(data, profile)
        assert "PROJECTS" not in text

    def test_empty_projects_with_empty_bullets_not_rendered(self, profile):
        data = {
            "title": "SDE",
            "summary": "Good.",
            "skills": {"Languages": "Python"},
            "experience": [{"header": "SDE | Co", "bullets": [{"text": "Did work"}]}],
            "projects": [{"header": "My Project", "bullets": []}],
            "education": "MIT",
        }
        text = assemble_resume_text(data, profile)
        assert "PROJECTS" not in text  # header exists but no bullets = skip

    def test_empty_summary_not_rendered(self, profile):
        data = {
            "title": "SDE",
            "summary": "",
            "skills": {"Languages": "Python"},
            "experience": [{"header": "SDE | Co", "bullets": [{"text": "Work"}]}],
            "education": "MIT",
        }
        text = assemble_resume_text(data, profile)
        assert "SUMMARY" not in text

    def test_empty_skills_not_rendered(self, profile):
        data = {
            "title": "SDE",
            "summary": "Good.",
            "skills": {},
            "experience": [{"header": "SDE | Co", "bullets": [{"text": "Work"}]}],
            "education": "MIT",
        }
        text = assemble_resume_text(data, profile)
        assert "TECHNICAL SKILLS" not in text

    def test_no_na_anywhere(self, profile):
        data = {
            "title": "SDE",
            "summary": "Good.",
            "skills": {"Languages": "Python"},
            "experience": [{"header": "SDE | Co", "bullets": [{"text": "Work"}]}],
            "projects": [],
            "education": "MIT",
        }
        text = assemble_resume_text(data, profile)
        assert "N/A" not in text

    def test_full_resume_has_all_sections(self, profile):
        data = {
            "title": "SDE",
            "summary": "Good engineer.",
            "skills": {"Languages": "Python, Java"},
            "experience": [{"header": "SDE | Co | 2024 - Present", "bullets": [{"text": "Built API"}]}],
            "projects": [{"header": "MyApp", "bullets": [{"text": "Built app"}]}],
            "education": "MIT | BS | CS | 2022",
        }
        text = assemble_resume_text(data, profile)
        assert "SUMMARY" in text
        assert "TECHNICAL SKILLS" in text
        assert "EXPERIENCE" in text
        assert "PROJECTS" in text
        assert "EDUCATION" in text
