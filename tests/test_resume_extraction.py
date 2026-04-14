"""Tests for resume.extraction and resume.validation submodules."""

import pytest
from applypilot.resume.extraction import (
    _coerce_str,
    _coerce_list,
    _merge_unique,
    get_profile_skill_sections,
    get_profile_skill_keywords,
    get_profile_company_names,
    get_profile_project_names,
    get_profile_school_names,
    get_profile_verified_metrics,
)


class TestCoerceStr:
    def test_none(self):
        assert _coerce_str(None) == ""

    def test_string(self):
        assert _coerce_str("  hello  ") == "hello"

    def test_number(self):
        assert _coerce_str(42) == "42"


class TestCoerceList:
    def test_list(self):
        assert _coerce_list(["a", "b", ""]) == ["a", "b"]

    def test_string(self):
        assert _coerce_list("hello") == ["hello"]

    def test_none(self):
        assert _coerce_list(None) == []


class TestMergeUnique:
    def test_dedup(self):
        assert _merge_unique(["Python", "Go"], ["python", "Rust"]) == ["Python", "Go", "Rust"]

    def test_empty(self):
        assert _merge_unique([], ["a"]) == ["a"]


class TestProfileExtraction:
    @pytest.fixture
    def profile(self):
        return {
            "skills": [
                {"name": "Languages", "keywords": ["Python", "Go"]},
                {"name": "Tools", "keywords": ["Docker"]},
            ],
            "work": [
                {"company": "Acme", "key_metrics": ["50% faster"]},
                {"company": "Beta"},
            ],
            "projects": [{"name": "MyApp"}, {"name": "CLI Tool"}],
            "education": [{"institution": "MIT"}, {"institution": "Stanford"}],
        }

    def test_skill_sections(self, profile):
        sections = get_profile_skill_sections(profile)
        assert len(sections) == 2
        assert sections[0] == ("Languages", ["Python", "Go"])

    def test_skill_keywords(self, profile):
        kws = get_profile_skill_keywords(profile)
        assert "Python" in kws
        assert "Docker" in kws

    def test_company_names(self, profile):
        assert get_profile_company_names(profile) == ["Acme", "Beta"]

    def test_project_names(self, profile):
        assert get_profile_project_names(profile) == ["MyApp", "CLI Tool"]

    def test_school_names(self, profile):
        assert get_profile_school_names(profile) == ["MIT", "Stanford"]

    def test_verified_metrics(self, profile):
        assert get_profile_verified_metrics(profile) == ["50% faster"]

    def test_empty_profile(self):
        assert get_profile_skill_sections({}) == []
        assert get_profile_company_names({}) == []
